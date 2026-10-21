"""A noite do debate num comando só: áudio -> falas -> sessão de checagem -> tela de aprovação (e publicador).

O que roda junto (ver `Noite`):
- a linha de falas (scripts/falas.py): loopback do som do Windows, gravado em arquivo, ou um arquivo de
  áudio no ritmo real ou acelerado. Se ela cai, o erro vai para a tela e ela recomeça sozinha (ao vivo,
  numa gravação nova);
- a sessão (scripts/sessao.py), gravada em <pasta>/eventos.jsonl; cada fala fechada também vai para
  <pasta>/falas.jsonl antes de chegar à sessão, e o arquivo de dados da página para <pasta>/painel.json;
- a tela de aprovação (scripts/aprovacao.py), com o painel de erros por etapa;
- a atualização da base a cada 10 min (base_checagens.atualiza -> Sessao.atualiza_base);
- o publicador (scripts/publicador.py), com --publicar.

O 00:00 é marcado uma vez (botão da tela ou Enter no terminal) e vale para a sessão (minutagem e
aquecimento de 10 min da publicação automática) e para a linha de falas. A publicação automática usa o
limiar de <dados>/avaliacao/limiar.json (sem ele, ou medido com outro modelo, desligada). Ao vivo, o
aquecimento conta no relógio; com `arquivo` e `pos-debate`, no relógio da gravação (a minutagem da fala),
para o repasse depois do debate não publicar sozinho os 10 primeiros minutos.

Retomada: rodar o mesmo comando com a mesma --pasta reconstrói a sessão pelo registro, termina as
análises interrompidas, entrega à sessão as falas que ficaram só em falas.jsonl e segue a partir da última
fala entregue, sem chamar o Claude de novo para o que já está no registro e sem republicar.

Uso:
  python -P scripts/noite.py ao-vivo  [opções]                     # loopback; Enter marca o 00:00
  python -P scripts/noite.py arquivo AUDIO --zero S [--inicio S] [--fim S] [--ritmo 1|0] [opções]
  python -P scripts/noite.py pos-debate [GRAVACAO.wav ...] [--zero S] [opções]
      a mesma sessão sobre a(s) gravação(ões) da noite, no ritmo da CPU, gerando o painel completo
      (meta.encerrado). Sem GRAVACAO, usa <pasta>/gravacoes/*.wav, cada uma com o 00:00 do .json ao lado.
      Continua a sessão da pasta a partir da última fala entregue.

Opções comuns: --dados D (CHECAGEM_DADOS ou data/ do checkout principal) · --pasta P (padrão
D/sessao_globo) · --porta 8765 · --prompt/--vocabulario (Whisper) · --sem-base · --intervalo-base 600 · --explorer-emergencia ·
--publicar [--repo R] [--remoto origin] [--ramo main] [--intervalo 60].
"""
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comum import BRASILIA, le_jsonl, pasta_dados  # noqa: E402
from sessao import Sessao, conteudo_publico  # noqa: E402


class Noite:
    def __init__(self, pasta, indice, fabrica_linha, chamar=None, relogio=None, meta=None, paralelo=3,
                 recomeca_se_acabar=False, reinicios=20, espera_reinicio=5.0, atualiza_base=None,
                 intervalo_base=600.0, porta=None, publicador=None, alerta_sem_fala_s=None, log=print,
                 esperas_entre_tentativas=None, relogio_da_gravacao=False):
        """pasta: pasta da sessão. fabrica_linha(desde_s) -> linha de falas (iterável de falas.Fala, com
        marcar_zero(instante) e parar()) que entrega só as falas que terminam depois de `desde_s` (segundos
        desde o 00:00; None = do começo). chamar, relogio, meta: os da Sessao. recomeca_se_acabar: ao vivo,
        a linha só acaba quando mandam parar; se acabar antes, é tratada como queda. reinicios: quantas
        vezes a linha recomeça depois de cair, esperando `espera_reinicio` s entre uma e outra.
        atualiza_base(ao_erro) -> registros novos da base (base_checagens.atualiza), chamada logo ao começar
        e depois a cada `intervalo_base` s contados do começo da anterior; ao_erro(mensagem) leva falhas
        parciais (buscas do Google que falharam) à tela. porta: sobe a tela de aprovação nessa porta (0 =
        qualquer livre; None = sem tela), em `self.tela`. publicador: publicador.Publicador, avisado a cada
        mudança pública; ao começar, só recebe o estado se ele difere do que o remoto já tem.
        alerta_sem_fala_s: ao vivo, avisa na tela (uma vez) quando passa esse tempo sem nenhuma fala
        depois do 00:00 (g1 parado, som no dispositivo errado). esperas_entre_tentativas: o da Sessao
        (padrão: sessao.ESPERAS_ENTRE_TENTATIVAS_S). relogio_da_gravacao: pós-debate; os 10 min de
        aquecimento da publicação automática contam no áudio (Sessao(aquecimento_pela_gravacao))."""
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._fabrica_linha = fabrica_linha
        self._recomeca_se_acabar = recomeca_se_acabar
        self._reinicios, self._espera_reinicio = reinicios, espera_reinicio
        self._atualiza_base, self._intervalo_base = atualiza_base, intervalo_base
        self._log = log
        self.publicador = publicador
        self._ja_entregues = le_jsonl(self.pasta / "falas.jsonl")  # falas de rodadas anteriores
        # fim (s desde o 00:00) da última fala entregue à sessão: a linha nova segue dali
        self._ultimo_fim = max((f["fim_s"] for f in self._ja_entregues), default=None)
        self.sessao = Sessao.abre(self.pasta, indice, chamar=chamar, relogio=relogio, meta=meta,
                                  ao_mudar_publicado=self._mudou_publicado, ao_marcar_inicio=self._zero_da_linha,
                                  aquecimento_pela_gravacao=relogio_da_gravacao,
                                  **({"esperas_entre_tentativas": esperas_entre_tentativas}
                                     if esperas_entre_tentativas is not None else {}))
        self._analises = ThreadPoolExecutor(max_workers=paralelo, thread_name_prefix="analise")
        self._falas = (self.pasta / "falas.jsonl").open("a", encoding="utf-8")
        self._trava = threading.Lock()
        self._linha = None
        self._linha_acabou = threading.Event()
        self._threads = []
        self._parar = threading.Event()
        self._porta = porta
        self.tela = None
        self._n_falas, self._ultima_fala, self._estado_linha, self._base = 0, None, "começando", None
        self._pronto = threading.Event()  # análises interrompidas retomadas: falas novas podem entrar
        self._alerta_sem_fala_s = alerta_sem_fala_s
        self._ultimo_sinal = time.monotonic()  # última fala, 00:00 marcado ou linha (re)começando

    # ---------- interface ----------

    def comeca(self):
        dados = self.sessao.exporta()
        self._grava_painel(dados)
        if self.publicador and self.publicador.difere_do_remoto(dados, conteudo_publico):
            self.publicador.notifica(dados)
        if self._porta is not None:
            from aprovacao import serve

            self.tela = serve(self.sessao, self._porta, self.situacao)
        self._inicia(self._retoma, "retomada")
        if self._alerta_sem_fala_s:
            self._inicia(self._vigia, "vigia")
        self._inicia(self._roda_linha, "linha")
        if self._atualiza_base:
            self._inicia(self._roda_base, "base")

    def marca_zero(self, instante=None):
        """O 00:00 (Enter no terminal; a tela chama sessao.marca_inicio, que dá no mesmo)."""
        self.sessao.marca_inicio(instante)

    def situacao(self):
        """O que a tela mostra da noite, no cabeçalho: a linha, as falas entregues, o último áudio que
        chegou da fonte e o nível do som no último segundo (a linha diz, se souber: ultimo_audio() em
        segundos de time.time(), nivel_audio() em dBFS), a base e o publicador (Publicador.situacao)."""
        with self._trava:
            linha = self._linha
        ultimo = getattr(linha, "ultimo_audio", lambda: None)()
        return {"linha": self._estado_linha, "falas": self._n_falas, "ultima_fala_em": self._ultima_fala,
                "ultimo_audio_em": datetime.fromtimestamp(ultimo, BRASILIA).isoformat(timespec="milliseconds")
                if ultimo else None,
                "nivel_audio_db": getattr(linha, "nivel_audio", lambda: None)(),
                "base_atualizada_em": self._base,
                "publicacao": self.publicador.situacao() if self.publicador else None}

    def espera(self, timeout=None):
        """Espera a linha acabar (fonte por arquivo) e as análises das falas dela. True se acabou."""
        if not (self._linha_acabou.wait(timeout) and self._pronto.wait(timeout)):
            return False
        self._analises.shutdown(wait=True)
        return True

    def encerra(self):
        self._parar.set()
        with self._trava:
            linha = self._linha
        if linha:
            linha.parar()
        for th in self._threads:  # a linha entrega a última fala (o que estava no Whisper) antes de parar
            if th.name == "linha":
                th.join(60)
        self._linha_acabou.set()
        if self._threads:  # comecou: a retomada termina antes de as análises fecharem
            self._pronto.wait(60)
        self._analises.shutdown(wait=True)
        if self.tela:
            self.tela.shutdown()
        self._grava_painel(self.sessao.exporta())
        self._falas.close()
        if self.publicador:
            self._log("enviando o último painel...")
            if not self.publicador.encerra():
                self._log("ATENÇÃO: o último painel não foi enviado (ver os erros de publicação)")

    # ---------- retomada ----------

    def _retoma(self):
        """Depois de um travamento: termina as análises que ficaram pela metade (só elas chamam o
        Claude) e entrega à sessão as falas que chegaram a falas.jsonl mas não ao registro dela. Só
        então as falas novas da linha entram."""
        try:
            self.sessao.retoma_analises()
            na_sessao = Counter((e["fala"]["quem"], e["fala"]["inicio_s"], e["fala"]["texto"])
                                for e in self.sessao.eventos if e["tipo"] == "fala")
            for f in self._ja_entregues:
                chave = (f["quem"], f["inicio_s"], f["texto"])
                if na_sessao[chave]:
                    na_sessao[chave] -= 1
                else:
                    self._analises.submit(self._recebe, *chave, f.get("atraso_s"))
        except Exception as e:  # noqa: BLE001
            self.sessao.registra_erro("sessao", f"retomada: {type(e).__name__}: {e}")
        finally:
            self._pronto.set()

    # ---------- linha de falas ----------

    def _roda_linha(self):
        """Roda a linha; se ela cai (ou, ao vivo, acaba sem ninguém mandar parar), o erro vai para a tela
        e uma linha nova começa depois da última fala entregue, com o mesmo 00:00."""
        tentativas = 0
        try:
            while not self._parar.is_set():
                try:
                    with self._trava:
                        self._linha = self._fabrica_linha(self._ultimo_fim)
                    if inicio := self._inicio():
                        self._linha.marcar_zero(inicio)
                    self._estado_linha = "rodando"
                    self._ultimo_sinal = time.monotonic()
                    for fala in self._linha:
                        self._entrega(fala)
                    if self._parar.is_set() or not self._recomeca_se_acabar:
                        return
                    raise RuntimeError("a fonte de áudio parou de entregar som")
                except Exception as e:  # noqa: BLE001 - a noite segue; o operador vê na tela
                    if self._parar.is_set():
                        return
                    tentativas += 1
                    self.sessao.registra_erro("transcricao", f"a linha de falas caiu ({type(e).__name__}: {e}); "
                                                             + ("recomeçando" if tentativas <= self._reinicios
                                                                else "desisti depois de várias tentativas"))
                    self._estado_linha = "caiu; recomeçando"
                    if tentativas > self._reinicios or self._parar.wait(self._espera_reinicio):
                        return
        finally:
            self._estado_linha = "parada"
            self._linha_acabou.set()

    def _inicio(self):
        """O 00:00 marcado na sessão (datetime), ou None."""
        inicio = next((e for e in reversed(self.sessao.eventos) if e["tipo"] == "inicio"), None)
        return datetime.fromisoformat(inicio["instante"]) if inicio else None

    def _vigia(self):
        avisado = False
        while not self._parar.wait(min(1.0, self._alerta_sem_fala_s / 3)):
            parado = time.monotonic() - self._ultimo_sinal
            if self._inicio() is None or parado < self._alerta_sem_fala_s:
                avisado = False
            elif not avisado:
                avisado = True
                self.sessao.registra_erro("audio", f"nenhuma fala há {parado:.0f} s depois do 00:00: o g1 está "
                                                   "tocando, com som, no dispositivo de saída padrão?")

    def _zero_da_linha(self, instante):
        self._ultimo_sinal = time.monotonic()
        with self._trava:
            if self._linha:
                self._linha.marcar_zero(instante)

    def _entrega(self, fala):
        if self._ultimo_fim is not None and fala.fim_s <= self._ultimo_fim:
            return  # já entregue por uma linha anterior
        with self._trava:
            self._falas.write(json.dumps({**asdict(fala), "entregue_em": _agora()}, ensure_ascii=False) + "\n")
            self._falas.flush()
            os.fsync(self._falas.fileno())
            self._ultimo_fim = fala.fim_s
            self._n_falas += 1
            self._ultimo_sinal = time.monotonic()
            self._ultima_fala = _agora()
        self._log(_texto_da_fala(fala))
        self._pronto.wait()
        self._analises.submit(self._recebe, fala.quem, fala.inicio_s, fala.texto, fala.atraso_s)

    def _recebe(self, quem, inicio_s, texto, atraso_s=None):
        """A fala na sessão. Falhas do Claude a sessão já registra (etapas extrair e confirmar); o que
        mais escapar vai para a tela em vez de sumir na thread."""
        try:
            self.sessao.recebe_fala(quem, inicio_s, texto, atraso_s=atraso_s)
        except Exception as e:  # noqa: BLE001
            self.sessao.registra_erro("sessao", f"fala de {quem} em {inicio_s:.0f} s: {type(e).__name__}: {e}")

    # ---------- base ----------

    def _roda_base(self):
        """Atualiza a base a cada intervalo (contado do começo da anterior); falhas vão para a tela."""
        avisa = lambda m: self.sessao.registra_erro("base", m)  # noqa: E731
        while not self._parar.is_set():
            comeco = time.monotonic()
            try:
                novos = self._atualiza_base(avisa)
                self._base = f"{datetime.now(BRASILIA):%H:%M} (+{len(novos)})"
                if novos and not self._parar.is_set():
                    self._log(f"[base] {datetime.now(BRASILIA):%H:%M:%S} {len(novos)} checagens novas")
                    self.sessao.atualiza_base(novos)
            except (Exception, SystemExit) as e:  # noqa: BLE001 - base_checagens encerra com SystemExit sem chave
                avisa(f"a atualização da base falhou ({type(e).__name__}: {e})")
            self._parar.wait(max(0.0, self._intervalo_base - (time.monotonic() - comeco)))

    # ---------- painel ----------

    def _mudou_publicado(self, dados):
        self._grava_painel(dados)
        if self.publicador:
            self.publicador.notifica(dados)

    def _grava_painel(self, dados):
        destino = self.pasta / "painel.json"
        tmp = destino.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, destino)

    def _inicia(self, alvo, nome):
        th = threading.Thread(target=alvo, name=nome, daemon=True)
        th.start()
        self._threads.append(th)


def _agora():
    return datetime.now(BRASILIA).isoformat(timespec="milliseconds")


def _texto_da_fala(f):
    m, seg = divmod(int(abs(f.inicio_s)), 60)
    sinal = "-" if f.inicio_s < 0 else ""  # começou antes do 00:00
    atraso = f" (+{f.atraso_s:.0f} s)" if f.atraso_s is not None else ""
    return f"[{sinal}{m // 60:d}:{m % 60:02d}:{seg:02d}] {f.quem}{atraso}: {f.texto}"


# ---------------------------------------------------------------- linhas de verdade (scripts/falas.py)

def zero_da_gravacao(wav, inicio=None):
    """O 00:00 em segundos da gravação `wav`: o `zero_s` marcado no .json ao lado ou, se a gravação começou
    depois do 00:00 (a linha caiu e recomeçou), o deslocamento entre o início da sessão (`inicio`,
    datetime) e o `comecou_em` do .json (horário de Brasília). None se não há como saber."""
    info = Path(wav).with_suffix(".json")
    if not info.exists():
        return None
    d = json.loads(info.read_text(encoding="utf-8"))
    if d.get("zero_s") is not None:
        return float(d["zero_s"])
    if inicio is None or not d.get("comecou_em"):
        return None
    comecou = datetime.fromisoformat(d["comecou_em"])
    if comecou.tzinfo is None:
        comecou = comecou.replace(tzinfo=BRASILIA)
    return round((inicio - comecou).total_seconds(), 3)


class LinhaAoVivo:
    """Loopback do som do Windows, gravado em <pasta>/gravacoes/gravacao-HHMMSS.wav (uma gravação nova a
    cada recomeço), com o 00:00 dado em horário de Brasília."""

    def __init__(self, pasta, transcritor, reconhecedor, dispositivo=None):
        from falas import FonteLoopback, LinhaDeFalas

        gravacao = Path(pasta) / "gravacoes" / f"gravacao-{datetime.now(BRASILIA):%H%M%S}.wav"
        self.fonte = FonteLoopback(gravacao, dispositivo)
        self.linha = LinhaDeFalas(self.fonte, transcritor, reconhecedor, registro=Path(pasta) / "linha.jsonl")

    def marcar_zero(self, instante):
        # o relógio da fonte segue o de parede (o loopback preenche com silêncio quando nada toca)
        self.linha.marcar_zero(self.fonte.posicao() - (datetime.now(BRASILIA) - instante).total_seconds())

    def parar(self):
        self.linha.parar()

    def ultimo_audio(self):
        return self.linha.ultimo_audio_s

    def nivel_audio(self):
        return self.linha.nivel_audio_db

    def __iter__(self):
        return iter(self.linha)


class LinhaDeArquivos:
    """Uma ou mais gravações em sequência, cada uma com o seu 00:00, entregando só as falas que terminam
    depois de `desde` (s desde o 00:00). O 00:00 já vem de cada arquivo: marcar_zero não muda nada."""

    def __init__(self, gravacoes, transcritor, reconhecedor, desde=None, ritmo=None, registro=None):
        """gravacoes: [(caminho, zero_s, inicio_s ou None, fim_s ou None)]. ritmo: 1 = tempo real; None =
        o mais rápido que a CPU der."""
        self.gravacoes, self.desde, self.ritmo = gravacoes, desde, ritmo
        self.transcritor, self.reconhecedor, self.registro = transcritor, reconhecedor, registro
        self._atual = None
        self._parar = threading.Event()

    def marcar_zero(self, instante):
        pass

    def parar(self):
        self._parar.set()
        if self._atual:
            self._atual.parar()

    def ultimo_audio(self):
        return self._atual.ultimo_audio_s if self._atual else None

    def nivel_audio(self):
        return self._atual.nivel_audio_db if self._atual else None

    def __iter__(self):
        from falas import FonteArquivo, LinhaDeFalas

        for caminho, zero, inicio, fim in self.gravacoes:
            if self._parar.is_set():
                return
            comeco = max(inicio or 0.0, zero + self.desde if self.desde is not None else 0.0)
            if fim is not None and comeco >= fim:
                continue
            self._atual = LinhaDeFalas(FonteArquivo(caminho, comeco, fim, self.ritmo), self.transcritor,
                                       self.reconhecedor, zero=zero, registro=self.registro)
            for fala in self._atual:
                if self.desde is None or fala.fim_s > self.desde:
                    yield fala


# ---------------------------------------------------------------- linha de comando

def _carrega_modelos(a, dados):
    from falas import Transcritor
    from vozes import Reconhecedor

    print("carregando Whisper, perfis de voz e Silero...", flush=True)
    return Transcritor(prompt=a.prompt, vocabulario=a.vocabulario), Reconhecedor.carregar(dados / "vozes")


def _gravacoes(a, pasta, inicio):
    """[(caminho, zero, inicio, fim)] do comando arquivo ou pos-debate."""
    if a.cmd == "arquivo":
        return [(a.audio, a.zero, a.inicio, a.fim)]
    arquivos = [Path(g) for g in a.gravacoes] or sorted((pasta / "gravacoes").glob("*.wav"))
    if not arquivos:
        sys.exit(f"nenhuma gravação: passe o arquivo ou confira {pasta / 'gravacoes'}")
    out = []
    for g in arquivos:
        zero = a.zero if a.zero is not None and len(arquivos) == 1 else zero_da_gravacao(g, inicio)
        if zero is None:
            sys.exit(f"{g}: sem 00:00 (nem zero_s no .json, nem 00:00 na sessão); passe --zero S")
        out.append((g, zero, None, None))
    return out


def main(argv=None):
    import argparse

    import agente
    import base_checagens
    from base_checagens import Indice, pasta_base
    from falas import PROMPT
    from sessao import MODELO, le_limiar

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    av = sub.add_parser("ao-vivo", help="loopback do som do Windows; Enter marca o 00:00")
    av.add_argument("--dispositivo", type=int, help="índice do PyAudio (padrão: loopback da saída padrão)")
    ar = sub.add_parser("arquivo", help="um arquivo de áudio no lugar do som do Windows (ensaio)")
    ar.add_argument("audio")
    ar.add_argument("--zero", type=float, required=True, help="00:00 em segundos do arquivo")
    ar.add_argument("--inicio", type=float, help="começa neste segundo do arquivo")
    ar.add_argument("--fim", type=float, help="termina neste segundo do arquivo")
    ar.add_argument("--ritmo", type=float, default=1.0, help="1 = tempo real (padrão); 0 = o mais rápido possível")
    pd = sub.add_parser("pos-debate", help="a sessão sobre a(s) gravação(ões), no ritmo da CPU, com meta.encerrado")
    pd.add_argument("gravacoes", nargs="*", help="padrão: PASTA/gravacoes/*.wav")
    pd.add_argument("--zero", type=float, help="00:00 em segundos da gravação (só com uma gravação sem .json)")
    for p in (av, ar, pd):
        p.add_argument("--dados", help="pasta de dados (padrão: CHECAGEM_DADOS ou data/ do checkout principal)")
        p.add_argument("--pasta", help="pasta da sessão (padrão: DADOS/sessao_globo)")
        p.add_argument("--porta", type=int, default=8765, help="porta da tela de aprovação (padrão 8765)")
        p.add_argument("--prompt", default=PROMPT, help="prompt inicial do Whisper")
        p.add_argument("--vocabulario", default=str(Path(__file__).resolve().parent.parent / "data" / "vocabulario.json"),
                       help="nomes do debate para o Whisper (JSON com 'termos')")
        p.add_argument("--paralelo", type=int, default=3, help="falas analisadas ao mesmo tempo (padrão 3)")
        p.add_argument("--sem-base", action="store_true", help="não atualiza a base durante a noite")
        p.add_argument("--explorer-emergencia", action="store_true",
                       help="reserva de emergência: atualiza a base pelo buscador interno do Fact Check Explorer "
                            "no lugar da API oficial (desligado por padrão)")
        p.add_argument("--intervalo-base", type=float, default=600, help="segundos entre atualizações (padrão 600)")
        p.add_argument("--publicar", action="store_true", help="envia o painel ao site (publicador.py)")
        p.add_argument("--repo", help="repositório do site (padrão: este)")
        p.add_argument("--remoto", default="origin")
        p.add_argument("--ramo", default="main")
        p.add_argument("--intervalo", type=float, default=60, help="segundos mínimos entre pushes (padrão 60)")
        p.add_argument("--sair-no-fim", action="store_true",
                       help="arquivo/pos-debate: sai quando a gravação acaba (padrão: a tela segue aberta para a fila)")
    a = ap.parse_args(argv)

    dados = pasta_dados(a.dados)
    pasta = Path(a.pasta) if a.pasta else dados / "sessao_globo"
    limiar, sem_limiar = le_limiar(dados)
    indice = Indice.da_pasta(pasta_base(dados))
    transcritor, reconhecedor = _carrega_modelos(a, dados)

    publicador = None
    sessao_ref = {}
    if a.publicar:
        from publicador import RAIZ as REPO_PADRAO
        from publicador import Publicador

        publicador = Publicador(a.repo or REPO_PADRAO, a.remoto, a.ramo, intervalo=a.intervalo,
                                ao_erro=lambda m: sessao_ref["s"].registra_erro("publicar", m),
                                ao_publicar=lambda c: print(f"[publicador] {datetime.now(BRASILIA):%H:%M:%S} "
                                                            f"{c[:8]} -> {a.remoto}/{a.ramo}", flush=True))

    def chamar(etapa, prompt, conteudo, schema):
        return agente.chama(etapa, prompt, conteudo, schema, logs=pasta / "agente", modelo=MODELO)

    atualiza_base = None
    if not a.sem_base:
        termos = base_checagens.termos_de_busca(Path(base_checagens.RAIZ) / "data" / "buscas_checagens.json")
        atualiza_base = lambda ao_erro: base_checagens.atualiza(  # noqa: E731
            pasta_base(dados), termos, None if a.explorer_emergencia else base_checagens.chave_google(), dias=1, log=lambda m: print(f"[base] {m}", flush=True),
            ao_erro=ao_erro, explorer=a.explorer_emergencia)

    if a.cmd == "ao-vivo":
        fabrica = lambda desde: LinhaAoVivo(pasta, transcritor, reconhecedor, a.dispositivo)  # noqa: E731
    else:
        gravacoes_ref = {}

        def fabrica(desde):
            if "g" not in gravacoes_ref:
                gravacoes_ref["g"] = _gravacoes(a, pasta, noite._inicio())
            return LinhaDeArquivos(gravacoes_ref["g"], transcritor, reconhecedor, desde,
                                   ritmo=(a.ritmo or None) if a.cmd == "arquivo" else None,
                                   registro=pasta / "linha.jsonl")

    noite = Noite(pasta, indice, fabrica, chamar=chamar, meta={"limiar_automatico": limiar,
                                                               **({"encerrado": True} if a.cmd == "pos-debate" else {})},
                  paralelo=a.paralelo, recomeca_se_acabar=a.cmd == "ao-vivo", atualiza_base=atualiza_base,
                  intervalo_base=a.intervalo_base, porta=a.porta, publicador=publicador,
                  alerta_sem_fala_s=180 if a.cmd == "ao-vivo" else None, log=lambda m: print(m, flush=True),
                  relogio_da_gravacao=a.cmd != "ao-vivo")  # arquivo e pós-debate: o relógio é o do áudio
    sessao_ref["s"] = noite.sessao
    if sem_limiar:  # o operador vê na tela por que nada sai sozinho
        noite.sessao.registra_erro("limiar", f"publicação automática desligada: {sem_limiar}", uma_vez=True)
    if a.cmd == "pos-debate" and noite._inicio() is None:
        _marca_inicio_pela_gravacao(noite, _gravacoes(a, pasta, None)[0])
    print(f"sessão: {pasta} ({len(noite.sessao.eventos)} eventos no registro) · base: {len(indice.registros)} checagens · "
          f"modelo: {MODELO} · publicação automática: "
          + (f"nota >= {limiar} depois de 10 min" if limiar is not None else f"desligada ({sem_limiar})")
          + (f" · publicador -> {a.remoto}/{a.ramo}" if publicador else " · sem publicar"), flush=True)
    noite.comeca()
    print(f"tela de aprovação: http://127.0.0.1:{noite.tela.server_address[1]}/", flush=True)
    _ctrl_break_encerra()
    try:
        if a.cmd == "ao-vivo":
            _terminal_ao_vivo(noite)
        else:
            while not noite.espera(timeout=1):  # com timeout: o Ctrl+C chega no Windows
                pass
            print("a gravação acabou: painel completo em " + str(pasta / "painel.json"), flush=True)
            if not a.sair_no_fim:
                print("a tela segue aberta para a fila. Ctrl+C encerra.", flush=True)
                _espera_ctrl_c()
    except KeyboardInterrupt:
        pass
    print("encerrando: parando o áudio, esperando as análises em curso e o último envio...", flush=True)
    noite.encerra()
    print(f"encerrada às {datetime.now(BRASILIA):%H:%M:%S}", flush=True)


def _marca_inicio_pela_gravacao(noite, gravacao):
    """Pós-debate numa pasta nova: o 00:00 da sessão é o horário em que a gravação chegou ao zero."""
    caminho, zero, _, _ = gravacao
    info = Path(caminho).with_suffix(".json")
    if info.exists() and (comecou := json.loads(info.read_text(encoding="utf-8")).get("comecou_em")):
        comecou = datetime.fromisoformat(comecou)
        comecou = comecou if comecou.tzinfo else comecou.replace(tzinfo=BRASILIA)
        noite.marca_zero(comecou + timedelta(seconds=zero))


def _espera_ctrl_c():
    while True:
        time.sleep(1)


def _ctrl_break_encerra():
    """Ctrl+Break (ou fechar a janela do console) encerra limpo, como o Ctrl+C."""
    import signal

    def interrompe(*_):
        raise KeyboardInterrupt

    for nome in ("SIGBREAK", "SIGTERM"):
        if hasattr(signal, nome):
            signal.signal(getattr(signal, nome), interrompe)


def _terminal_ao_vivo(noite):
    """Enter marca o 00:00; depois disso, remarcar exige digitar 'remarcar'. Ctrl+C encerra."""
    print("Capturando o som do computador. Enter marca o 00:00 (abertura do mediador); "
          "o botão da tela faz o mesmo. Ctrl+C encerra.", flush=True)
    while True:
        try:
            texto = input().strip().lower()
        except EOFError:  # sem terminal: espera o Ctrl+C
            _espera_ctrl_c()
        inicio = noite._inicio()
        if inicio is None or texto == "remarcar":
            noite.marca_zero()
            print(f"00:00 marcado às {noite._inicio():%H:%M:%S}.", flush=True)
        else:
            print(f"00:00 já marcado às {inicio:%H:%M:%S}. Para remarcar agora, digite 'remarcar' e Enter.", flush=True)


if __name__ == "__main__":
    main()
