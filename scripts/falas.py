"""Do áudio às falas: captura, detecção de voz, transcrição em trechos, quem fala e fechamento de fala.

O fluxo: fonte de áudio (loopback WASAPI do som do Windows, gravado em arquivo, ou um arquivo no ritmo
real ou acelerado) -> detecção de voz Silero (segmentos) -> faster-whisper large-v3-turbo int8 na CPU,
com o vocabulário do debate no prompt -> quem fala em janelas de ~3 s (perfis de voz do vozes.py) ->
fechamento de fala (muda quem fala, 60 s, ou silêncio longo) -> falas fechadas.

Uso como biblioteca (a sessão, ticket 10):
    from falas import FonteLoopback, FonteArquivo, LinhaDeFalas
    fonte = FonteLoopback(gravar_em="gravacao.wav")          # ou FonteArquivo(caminho, inicio, fim, ritmo=1.0)
    linha = LinhaDeFalas(fonte)                              # carrega Silero, Whisper e perfis de voz
    linha.marcar_zero()                                      # 00:00 = agora (ou marcar_zero(t) em segundos da fonte)
    for fala in linha:                                       # bloqueia; termina quando a fonte acaba ou com parar()
        sessao.recebe_fala(fala.quem, fala.inicio_s, fala.texto)

Linha de comando:
    python -P scripts/falas.py loopback --gravar G.wav [--saida F.jsonl]   # Enter marca o 00:00; Ctrl+C encerra
    python -P scripts/falas.py arquivo AUDIO [--inicio S] [--fim S] [--zero S] [--ritmo 1|0] [--saida F.jsonl]
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vozes import MEDIACAO, TAXA  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
DADOS = Path(os.environ.get("CHECAGEM_DADOS", r"C:\dev\debate_lulavsflavio\data"))
MODELO_WHISPER = "large-v3-turbo"
PROMPT = "Debate presidencial, segundo turno. Lula e Flávio Bolsonaro."
JANELA_VOZ_S = 3.0  # quem fala é decidido em janelas de pelo menos ~3 s (relatório do ticket 08)
MAX_FALA_S = 60.0
SILENCIO_S = 6.0  # silêncio que fecha a fala em andamento (intervalo, fim de bloco)
ATRASO_ECONOMIA_S = 30.0  # acima deste atraso do transcritor, quem fala usa o método mais barato
LOTE_MAX_S = 28.0  # o Whisper processa 30 s por vez: segmentos acumulados vão juntos até esse limite


@dataclass
class Pedaco:
    """Trecho transcrito e atribuído, em segundos da fonte."""
    quem: str | None
    inicio: float
    fim: float
    texto: str


@dataclass
class Fala:
    """Fala fechada, em segundos desde o 00:00. `atraso_s`: segundos entre o fim da fala no áudio e a
    entrega (só com fonte no ritmo real; None no acelerado)."""
    quem: str
    inicio_s: float
    fim_s: float
    texto: str
    atraso_s: float | None = field(default=None, compare=False)


class Fechador:
    """Junta pedaços (em segundos da fonte) em falas fechadas (em segundos desde o 00:00).

    Fecha a fala quando muda quem fala, quando passaria de `max_s`, depois de `silencio_s` sem fala
    (avisado por `tempo(t)`) ou no fim da fonte. Antes de o 00:00 ser marcado as falas ficam retidas;
    a marcação libera as que terminam depois dele (o 00:00 pode ser marcado com atraso)."""

    def __init__(self, zero=None, max_s=60.0, silencio_s=None):
        self.zero = zero
        self.max_s = max_s
        self.silencio_s = silencio_s
        self._atual = []
        self._retidas = []  # fechadas antes de o 00:00 ser marcado, em segundos da fonte

    def marcar_zero(self, t):
        self.zero = t
        retidas, self._retidas = self._retidas, []
        return [f for r in retidas if (f := self._relativa(r))]

    def recebe(self, p):
        fechadas = []
        quem = self._quem()
        muda = p.quem is not None and quem is not None and p.quem != quem
        if self._atual and (muda or p.fim - self._atual[0].inicio > self.max_s):
            fechadas += self._fecha()
        self._atual.append(p)
        return fechadas

    def tempo(self, t):
        if self._atual and self.silencio_s is not None and t - self._atual[-1].fim >= self.silencio_s:
            return self._fecha()
        return []

    def fim(self):
        return self._fecha() if self._atual else []

    def _quem(self):
        return next((p.quem for p in self._atual if p.quem is not None), None)

    def _fecha(self):
        quem = self._quem() or MEDIACAO  # só pedaços curtos demais: voz não reconhecida
        ps, self._atual = self._atual, []
        bruta = (quem, ps[0].inicio, ps[-1].fim, " ".join(p.texto.strip() for p in ps if p.texto.strip()))
        if self.zero is None:
            self._retidas.append(bruta)
            return []
        return [f] if (f := self._relativa(bruta)) else []

    def _relativa(self, bruta):
        """Em segundos desde o 00:00; None para a fala que terminou antes dele."""
        quem, ini, fim, texto = bruta
        if fim <= self.zero:
            return None
        return Fala(quem, round(max(0.0, ini - self.zero), 2), round(fim - self.zero, 2), texto)


# ---------------------------------------------------------------- fontes de áudio
# Uma fonte entrega blocos float32 mono 16 kHz por `blocos()`; `inicio` é o segundo da fonte da primeira
# amostra e `posicao()` o segundo da fonte até onde já entregou áudio. Segundos da fonte = segundos do
# arquivo (fonte por arquivo) ou desde o início da gravação (loopback).

class FonteArquivo:
    """Trecho [inicio, fim] de um arquivo de áudio, decodificado pelo ffmpeg. ritmo=1.0 entrega no tempo
    real (como se fosse ao vivo); ritmo=2.0 no dobro; ritmo=None o mais rápido possível."""

    BLOCO = 1600  # 0,1 s

    def __init__(self, caminho, inicio=0.0, fim=None, ritmo=1.0):
        self.caminho, self.inicio, self.fim, self.ritmo = str(caminho), float(inicio), fim, ritmo
        self._amostras = 0
        self._parar = threading.Event()

    def posicao(self):
        return self.inicio + self._amostras / TAXA

    def parar(self):
        self._parar.set()

    def blocos(self):
        cmd = ["ffmpeg", "-loglevel", "error", "-ss", str(self.inicio)]
        if self.fim is not None:
            cmd += ["-to", str(self.fim)]
        cmd += ["-i", self.caminho, "-ac", "1", "-ar", str(TAXA), "-f", "s16le", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        t0 = time.monotonic()
        try:
            while not self._parar.is_set():
                raw = proc.stdout.read(self.BLOCO * 2)
                if not raw:
                    break
                bloco = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768
                if self.ritmo:
                    espera = t0 + (self._amostras + len(bloco)) / TAXA / self.ritmo - time.monotonic()
                    if espera > 0:
                        time.sleep(espera)
                self._amostras += len(bloco)
                yield bloco
        finally:
            proc.kill()
            proc.wait()


class FonteLoopback:
    """Som do Windows (o que toca no dispositivo de saída padrão), por loopback WASAPI (PyAudioWPatch).
    Grava tudo, já em 16 kHz mono, em `gravar_em` (WAV, gravado na thread da captura: a gravação não
    depende do transcritor dar conta). Ao lado, `<gravar_em>.json` guarda o início e o 00:00 marcado."""

    def __init__(self, gravar_em, dispositivo=None):
        self.gravar_em = Path(gravar_em)
        self.dispositivo = dispositivo  # índice do PyAudio; None = loopback da saída padrão
        self.inicio = 0.0
        self.ritmo = 1.0
        self._amostras = 0
        self._parar = threading.Event()
        self._fila = queue.Queue()
        self.info = {}

    def posicao(self):
        return self._amostras / TAXA

    def parar(self):
        self._parar.set()

    def anota(self, **campos):
        """Acrescenta campos ao arquivo .json ao lado da gravação (ex.: o 00:00 marcado)."""
        self.info.update(campos)
        self.gravar_em.with_suffix(".json").write_text(json.dumps(self.info, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")

    def blocos(self):
        import pyaudiowpatch as pyaudio
        import soundfile as sf
        import soxr

        pa = pyaudio.PyAudio()
        dev = pa.get_default_wasapi_loopback() if self.dispositivo is None \
            else pa.get_device_info_by_index(self.dispositivo)
        canais, taxa = int(dev["maxInputChannels"]), int(dev["defaultSampleRate"])
        reamostra = soxr.ResampleStream(taxa, TAXA, 1, dtype="float32")
        self.gravar_em.parent.mkdir(parents=True, exist_ok=True)
        wav = sf.SoundFile(str(self.gravar_em), "w", TAXA, 1, subtype="PCM_16")
        self.anota(dispositivo=dev["name"], taxa_original=taxa, canais=canais, taxa=TAXA,
                   comecou_em=datetime.now().isoformat(timespec="milliseconds"))
        brutos = queue.Queue()
        prontos = queue.Queue()

        def callback(dados, n, info, status):
            brutos.put(dados)
            return (None, pyaudio.paContinue)

        def grava():
            """Reamostra, preenche com silêncio os buracos (o loopback WASAPI não entrega nada enquanto
            nada toca, e o relógio da fonte tem que seguir o relógio de parede), grava e repassa."""
            t0, escritas, inserido, ultimo_flush = time.monotonic(), 0, 0, time.monotonic()
            while not self._parar.is_set():
                try:
                    dados = brutos.get(timeout=0.1)
                except queue.Empty:
                    dados = None
                if dados is not None:
                    x = np.frombuffer(dados, dtype=np.int16).reshape(-1, canais).astype(np.float32).mean(axis=1)
                    y = reamostra.resample_chunk(x / 32768)
                else:
                    falta = int((time.monotonic() - t0 - 0.3) * TAXA) - escritas
                    y = np.zeros(max(0, falta), dtype=np.float32)
                    inserido += len(y)
                if len(y):
                    wav.write(y)
                    escritas += len(y)
                    prontos.put(y)
                if time.monotonic() - ultimo_flush > 5:
                    wav.flush()  # o cabeçalho fica válido mesmo se o processo cair
                    ultimo_flush = time.monotonic()
            wav.close()
            self.anota(terminou_em=datetime.now().isoformat(timespec="milliseconds"),
                       segundos_gravados=round(escritas / TAXA, 2), silencio_inserido_s=round(inserido / TAXA, 2))

        stream = pa.open(format=pyaudio.paInt16, channels=canais, rate=taxa, input=True,
                         input_device_index=dev["index"], frames_per_buffer=taxa // 20, stream_callback=callback)
        gravador = threading.Thread(target=grava, daemon=True)
        gravador.start()
        try:
            while not self._parar.is_set():
                try:
                    y = prontos.get(timeout=0.2)
                except queue.Empty:
                    continue
                self._amostras += len(y)
                yield y
        finally:
            self._parar.set()
            gravador.join()
            stream.stop_stream()
            stream.close()
            pa.terminate()


# ---------------------------------------------------------------- transcrição e quem fala

class Transcritor:
    """faster-whisper na CPU, com o vocabulário do debate no prompt de cada trecho."""

    def __init__(self, modelo=MODELO_WHISPER, prompt=PROMPT, vocabulario=RAIZ / "data" / "vocabulario.json",
                 beam_size=5, threads=8):
        from faster_whisper import WhisperModel

        termos = json.loads(Path(vocabulario).read_text(encoding="utf-8"))["termos"] if vocabulario else []
        self.prompt = prompt + (" Citados: " + ", ".join(termos) + "." if termos else "")
        self.beam_size = beam_size
        self.modelo = WhisperModel(modelo, device="cpu", compute_type="int8", cpu_threads=threads)

    def palavras(self, sinal):
        """[(início, fim, palavra)] em segundos do sinal. O Whisper às vezes repete um bloco de palavras
        com os mesmos tempos: palavras que voltam no tempo são descartadas."""
        segs, _ = self.modelo.transcribe(sinal, language="pt", word_timestamps=True, vad_filter=False,
                                         beam_size=self.beam_size, condition_on_previous_text=False,
                                         initial_prompt=self.prompt)
        out = []
        for s in segs:
            for w in s.words or []:
                if not out or w.start >= out[-1][1] - 0.05:
                    out.append((w.start, w.end, w.word))
        return out


def janelas_de_palavras(palavras, minimo=JANELA_VOZ_S, resto=1.5):
    """Agrupa palavras consecutivas em janelas de pelo menos `minimo` s; uma sobra final menor que
    `resto` s junta-se à janela anterior."""
    jan = []
    for w in palavras:
        if jan and jan[-1][-1][1] - jan[-1][0][0] < minimo:
            jan[-1].append(w)
        else:
            jan.append([w])
    if len(jan) > 1 and jan[-1][-1][1] - jan[-1][0][0] < resto:
        sobra = jan.pop()
        jan[-1] += sobra
    return jan


def _pulado(palavras, dur):
    """Segmento que o Whisper parece ter pulado: sem palavras, ou com palavras em menos de 30% dele."""
    if dur < 1.0:
        return False
    return not palavras or (dur >= 5.0 and palavras[-1][1] - palavras[0][0] < 0.3 * dur)


def _texto(palavras):
    return "".join(w for _, _, w in palavras).strip()


def atribui_janelas(sinal, palavras, reconhecedor):
    """Quem fala em janelas fixas de ~3 s de palavras: [(quem, início, fim, texto, semelhança)]."""
    out = []
    for jan in janelas_de_palavras(palavras):
        s, e = jan[0][0], jan[-1][1]
        v = reconhecedor.quem_fala(sinal[int(s * TAXA): int(e * TAXA)])
        out.append((v.quem, s, e, _texto(jan), round(v.semelhanca, 3) if v.quem else None))
    return out


def atribui_viterbi(sinal, palavras, reconhecedor, janela=1.5, passo=0.5, troca=0.5, limiar_curto=0.35,
                    minimo_s=JANELA_VOZ_S):
    """Quem fala palavra a palavra: semelhanças em janelas deslizantes de 1,5 s (passo 0,5 s), suavizadas
    por Viterbi com custo `troca` por mudança de locutor (Mediação vale `limiar_curto`, o limiar para
    trechos curtos). As palavras seguidas com o mesmo locutor viram um pedaço. Segmentos de menos de
    `minimo_s` vão para atribui_janelas."""
    if not palavras:
        return []
    dur = len(sinal) / TAXA
    if dur < minimo_s:
        return atribui_janelas(sinal, palavras, reconhecedor)
    nomes = list(reconhecedor.perfis) + [MEDIACAO]
    inicios = np.arange(0.0, max(dur - janela, 0.0) + 1e-9, passo)
    pont = []
    for a in inicios:  # (embeddings em lote não ficaram mais rápidos nesta CPU)
        emb = reconhecedor.embedding(sinal[int(a * TAXA): int((a + janela) * TAXA)])
        pont.append([float(reconhecedor.perfis[k] @ emb) for k in nomes[:-1]] + [limiar_curto])
    pont = np.array(pont)
    n, k = pont.shape
    acum, de = pont[0].copy(), np.zeros((n, k), dtype=int)
    for i in range(1, n):
        cand = acum[None, :] - troca * (1 - np.eye(k))  # cand[j, i'] = vir de i' para j
        de[i] = cand.argmax(axis=1)
        acum = cand.max(axis=1) + pont[i]
    estado = [int(acum.argmax())]
    for i in range(n - 1, 0, -1):
        estado.append(de[i][estado[-1]])
    estado = estado[::-1]
    centros = inicios + janela / 2
    out = []
    for w in palavras:
        i = int(np.abs(centros - (w[0] + w[1]) / 2).argmin())
        quem = nomes[estado[i]]
        if out and out[-1][0] == quem:
            out[-1][1].append(w)
        else:
            out.append((quem, [w]))
    res = []
    for quem, ws in out:
        a, b = int(ws[0][0] / passo), int(ws[-1][1] / passo)
        sem = float(pont[max(0, min(a, n - 1)): max(a + 1, min(b, n)), nomes.index(quem)].mean()) \
            if quem != MEDIACAO else None
        res.append((quem, ws[0][0], ws[-1][1], _texto(ws), round(sem, 3) if sem is not None else None))
    return res


ATRIBUICOES = {"janelas": atribui_janelas, "viterbi": atribui_viterbi}


# ---------------------------------------------------------------- a linha toda

class LinhaDeFalas:
    """Da fonte de áudio às falas fechadas. Itere sobre ela (bloqueia até a próxima fala) ou use
    `rodar(ao_fechar)`. `marcar_zero(t=None)` pode ser chamado de outra thread a qualquer momento.

    Threads: a fonte (captura), a detecção de voz e o transcritor (Whisper + quem fala + fechamento).
    Segmentos que chegam enquanto o Whisper trabalha vão juntos na próxima chamada (até ~28 s): o
    Whisper custa quase o mesmo para 3 s ou 30 s, então a linha se ajusta sozinha à carga da CPU."""

    def __init__(self, fonte, transcritor=None, reconhecedor=None, zero=None, max_fala_s=MAX_FALA_S,
                 silencio_s=SILENCIO_S, registro=None, vad_silencio_ms=500, vad_max_s=12.0, atribuicao="viterbi"):
        from vad import Chunker, SegmentAssembler, SileroVad
        from vozes import Reconhecedor

        self.fonte = fonte
        self.atribui = ATRIBUICOES[atribuicao]
        self.transcritor = transcritor or Transcritor()
        self.reconhecedor = reconhecedor or Reconhecedor.carregar()
        self.fechador = Fechador(zero=zero, max_s=max_fala_s, silencio_s=silencio_s)
        self.registro = open(registro, "a", encoding="utf-8") if registro else None
        self._vad = SileroVad()
        self._vad.load()
        self._chunker = Chunker()
        self._montador = SegmentAssembler(min_silence_ms=vad_silencio_ms, max_segment_s=vad_max_s)
        self._entrada = queue.Queue()  # ("seg", início, sinal) | ("tempo", t) | ("zero", t) | ("fim",)
        self._saida = queue.Queue()
        self._threads = []
        self.erro = None
        self.estatisticas = {"lotes": 0, "segundos_de_audio": 0.0, "segundos_de_cpu": 0.0}

    # ---------- interface

    def marcar_zero(self, t=None):
        """Marca o 00:00 no segundo `t` da fonte (None = o áudio que acabou de chegar). Devolve o t usado."""
        t = self.fonte.posicao() if t is None else float(t)
        self._entrada.put(("zero", t))
        if hasattr(self.fonte, "anota"):
            self.fonte.anota(zero_s=t)
        self._log({"evento": "zero", "t": t})
        return t

    def parar(self):
        self.fonte.parar()

    def rodar(self, ao_fechar):
        for fala in self:
            ao_fechar(fala)

    def __iter__(self):
        self._inicia()
        while True:
            item = self._saida.get()
            if item is None:
                break
            yield item
        for th in self._threads:
            th.join()
        if self.erro:
            raise self.erro

    # ---------- threads

    def _inicia(self):
        for alvo in (self._loop_vad, self._loop_transcricao):
            th = threading.Thread(target=self._protegido(alvo), daemon=True)
            th.start()
            self._threads.append(th)

    def _protegido(self, alvo):
        def roda():
            try:
                alvo()
            except BaseException as e:  # noqa: BLE001 - repassado ao iterador
                self.erro = e
                self.fonte.parar()
                self._entrada.put(("fim",))
                self._saida.put(None)
        return roda

    def _loop_vad(self):
        pos = 0  # amostras já passadas ao VAD
        inicio_seg = None
        ultimo_tempo = 0
        for bloco in self.fonte.blocos():
            for chunk in self._chunker.push(bloco):
                ev, audio = self._montador.feed(chunk, self._vad.prob(chunk))
                pos += len(chunk)
                if ev == "start":
                    inicio_seg = pos - self._montador.amostras
                elif ev == "segment":
                    self._entrada.put(("seg", self.fonte.inicio + inicio_seg / TAXA, audio))
                    inicio_seg = None
                if not self._montador.speech_active and pos - ultimo_tempo >= TAXA:
                    self._entrada.put(("tempo", self.fonte.inicio + pos / TAXA))
                    ultimo_tempo = pos
        resto = self._montador.flush()
        if resto is not None and inicio_seg is not None:
            self._entrada.put(("seg", self.fonte.inicio + inicio_seg / TAXA, resto))
        self._entrada.put(("fim",))

    def _loop_transcricao(self):
        pendentes = deque()
        while True:
            msgs = [pendentes.popleft() if pendentes else self._entrada.get()]
            if msgs[0][0] == "seg":
                n = len(msgs[0][2])
                while True:  # junta os segmentos que já estão esperando, até ~28 s, guardando a ordem
                    try:
                        prox = pendentes.popleft() if pendentes else self._entrada.get_nowait()
                    except queue.Empty:
                        break
                    if prox[0] == "fim" or (prox[0] == "seg" and n + len(prox[2]) > LOTE_MAX_S * TAXA):
                        pendentes.appendleft(prox)
                        break
                    msgs.append(prox)
                    n += len(prox[2]) if prox[0] == "seg" else 0
                lote = [m for m in msgs if m[0] == "seg"]
                pedacos = iter(self._pedacos(lote))
            for msg in msgs:
                if msg[0] == "seg":
                    for p in next(pedacos):
                        self._entrega(self.fechador.recebe(p))
                elif msg[0] == "tempo":
                    self._entrega(self.fechador.tempo(msg[1]))
                elif msg[0] == "zero":
                    self._entrega(self.fechador.marcar_zero(msg[1]))
                elif msg[0] == "fim":
                    self._entrega(self.fechador.fim())
                    self._saida.put(None)
                    return

    def _pedacos(self, lote):
        """Transcreve o lote numa chamada só (segmentos separados por 0,3 s de silêncio) e divide as
        palavras de cada segmento em janelas de ~3 s, cada uma com quem fala. Uma lista de pedaços por
        segmento."""
        c0 = time.perf_counter()
        pausa = np.zeros(int(0.3 * TAXA), dtype=np.float32)
        partes, deslocs, t = [], [], 0.0
        for _, ini, sinal in lote:
            deslocs.append((t, t + len(sinal) / TAXA, ini))
            partes += [sinal, pausa]
            t += (len(sinal) + len(pausa)) / TAXA
        palavras = self.transcritor.palavras(np.concatenate(partes))
        c1 = time.perf_counter()
        # atrasado demais (CPU disputada): quem fala pelas janelas fixas, ~4x mais baratas
        atrasado = self._ao_vivo() and             self.fonte.posicao() - (lote[-1][1] + len(lote[-1][2]) / TAXA) > ATRASO_ECONOMIA_S
        atribui = atribui_janelas if atrasado else self.atribui
        pedacos = []
        for k, (a, b, ini) in enumerate(deslocs):
            sinal = lote[k][2]
            ws = [(max(0.0, s - a), max(0.0, min(e, b) - a), w) for s, e, w in palavras if a - 0.15 <= s < b]
            if len(lote) > 1 and _pulado(ws, len(sinal) / TAXA):
                # o Whisper às vezes pula o começo do lote (o 1º texto sai muitos segundos depois): o
                # segmento sozinho costuma sair inteiro
                sozinho = self.transcritor.palavras(sinal)
                self._log({"evento": "repeticao", "inicio": round(ini, 3), "antes": len(ws), "depois": len(sozinho)})
                if len(sozinho) > len(ws):
                    ws = sozinho
            self._log({"evento": "segmento", "inicio": round(ini, 3), "dur": round(len(sinal) / TAXA, 3),
                       "palavras": [[round(s, 2), round(e, 2), w] for s, e, w in ws]})
            pedacos.append([])
            for quem, s, e, texto, sem in atribui(sinal, ws, self.reconhecedor):
                p = Pedaco(quem, round(ini + s, 3), round(ini + e, 3), texto)
                pedacos[-1].append(p)
                self._log({"evento": "pedaco", **asdict(p), "semelhanca": sem})
        c2 = time.perf_counter()
        dur = sum(len(s[2]) for s in lote) / TAXA
        fim_audio = lote[-1][1] + len(lote[-1][2]) / TAXA
        st = self.estatisticas
        st["lotes"] += 1
        st["segundos_de_audio"] += dur
        st["segundos_de_cpu"] += c2 - c0
        self._log({"evento": "lote", "segmentos": len(lote), "audio_s": round(dur, 2),
                   "whisper_s": round(c1 - c0, 2), "voz_s": round(c2 - c1, 2), "economia": atrasado,
                   "atraso_s": round(self.fonte.posicao() - fim_audio, 2)})
        return pedacos

    def _entrega(self, falas):
        for f in falas:
            if self._ao_vivo():
                f.atraso_s = round(self.fonte.posicao() - (f.fim_s + self.fechador.zero), 2)
            self._log({"evento": "fala", **asdict(f)})
            self._saida.put(f)

    def _ao_vivo(self):
        """Fonte no ritmo real (loopback ou arquivo com ritmo=1): atraso tem sentido."""
        return getattr(self.fonte, "ritmo", None) == 1.0

    def _log(self, d):
        if self.registro:
            self.registro.write(json.dumps({"em": datetime.now().isoformat(timespec="milliseconds"), **d},
                                           ensure_ascii=False) + "\n")
            self.registro.flush()


# ---------------------------------------------------------------- linha de comando

def _mostra(f):
    m, s = divmod(int(f.inicio_s), 60)
    atraso = f" (+{f.atraso_s:.1f} s)" if f.atraso_s is not None else ""
    print(f"[{m // 60:d}:{m % 60:02d}:{s:02d}] {f.quem}{atraso}: {f.texto}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lb = sub.add_parser("loopback", help="som do Windows; Enter marca o 00:00")
    lb.add_argument("--gravar", required=True, help="arquivo WAV da gravação contínua")
    lb.add_argument("--dispositivo", type=int)
    ar = sub.add_parser("arquivo", help="áudio de um arquivo, no ritmo real ou acelerado")
    ar.add_argument("audio")
    ar.add_argument("--inicio", type=float, default=0.0)
    ar.add_argument("--fim", type=float)
    ar.add_argument("--zero", type=float, help="00:00 em segundos do arquivo (padrão: --inicio)")
    ar.add_argument("--ritmo", type=float, default=1.0, help="1 = tempo real; 0 = o mais rápido possível")
    for p in (lb, ar):
        p.add_argument("--saida", help="registro JSONL (pedaços, lotes e falas)")
        p.add_argument("--prompt", default=PROMPT)
        p.add_argument("--vocabulario", default=str(RAIZ / "data" / "vocabulario.json"))
        p.add_argument("--dados", help="pasta de dados (padrão: CHECAGEM_DADOS ou C:\\dev\\debate_lulavsflavio\\data)")
    a = ap.parse_args(argv)
    from vozes import Reconhecedor

    dados = Path(a.dados) if a.dados else DADOS
    if a.cmd == "loopback":
        fonte, zero = FonteLoopback(a.gravar, a.dispositivo), None
    else:
        fonte = FonteArquivo(a.audio, a.inicio, a.fim, a.ritmo or None)
        zero = a.inicio if a.zero is None else a.zero
    linha = LinhaDeFalas(fonte, Transcritor(prompt=a.prompt, vocabulario=a.vocabulario),
                         Reconhecedor.carregar(dados / "vozes"), zero=zero, registro=a.saida)
    if a.cmd == "loopback":
        def espera_enter():
            input("Capturando. Enter marca o 00:00 (abertura do mediador).\n")
            print(f"00:00 marcado no segundo {linha.marcar_zero():.1f} da gravação.", flush=True)
        threading.Thread(target=espera_enter, daemon=True).start()
    try:
        linha.rodar(_mostra)
    except KeyboardInterrupt:
        linha.parar()
    st = linha.estatisticas
    if st["segundos_de_audio"]:
        print(f"fim: {st['lotes']} lotes, {st['segundos_de_audio']:.0f} s de fala, "
              f"{st['segundos_de_cpu'] / st['segundos_de_audio']:.2f} s de processamento por s de fala")


if __name__ == "__main__":
    main()
