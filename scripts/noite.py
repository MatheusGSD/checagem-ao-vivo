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
limiar de <dados>/avaliacao/limiar.json (sem ele, desligada).

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
D/sessao_globo) · --porta 8765 · --prompt/--vocabulario (Whisper) · --sem-base · --intervalo-base 600 ·
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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sessao import BRASILIA, Sessao  # noqa: E402


class SessaoDaNoite(Sessao):
    """A sessão de checagem, avisando a noite quando o 00:00 é marcado (pela tela ou pelo terminal)."""

    ao_marcar_inicio = None

    def marca_inicio(self, instante=None):
        super().marca_inicio(instante)
        if self.ao_marcar_inicio:
            inicio = next(e for e in reversed(self.eventos) if e["tipo"] == "inicio")
            self.ao_marcar_inicio(datetime.fromisoformat(inicio["instante"]))


class Noite:
    def __init__(self, pasta, indice, fabrica_linha, chamar=None, relogio=None, meta=None, paralelo=3,
                 recomeca_se_acabar=False, reinicios=20, espera_reinicio=5.0, atualiza_base=None,
                 intervalo_base=600.0, porta=None, publicador=None, alerta_sem_fala_s=None, log=print):
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
        depois do 00:00 (g1 parado, som no dispositivo errado)."""
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._fabrica_linha = fabrica_linha
        self._recomeca_se_acabar = recomeca_se_acabar
        self._reinicios, self._espera_reinicio = reinicios, espera_reinicio
        self._atualiza_base, self._intervalo_base = atualiza_base, intervalo_base
        self._log = log
        self.publicador = publicador
        self._ja_entregues = _le_jsonl(self.pasta / "falas.jsonl")  # falas de rodadas anteriores
        # fim (s desde o 00:00) da última fala entregue à sessão: a linha nova segue dali
        self._ultimo_fim = max((f["fim_s"] for f in self._ja_entregues), default=None)
        self.sessao = SessaoDaNoite.abre(self.pasta, indice, chamar=chamar, relogio=relogio, meta=meta,
                                         ao_mudar_publicado=self._mudou_publicado)
        self.sessao.ao_marcar_inicio = self._zero_da_linha
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
        if self.publicador and _difere_do_remoto(self.publicador, dados):
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
        """O que a tela mostra da noite, no cabeçalho."""
        return {"linha": self._estado_linha, "falas": self._n_falas, "ultima_fala_em": self._ultima_fala,
                "base_atualizada_em": self._base}

    def espera(self, timeout=None):
        """Espera a linha acabar (fonte por arquivo) e as análises das falas dela. True se acabou."""
        if not (self._linha_acabou.wait(timeout) and self._pronto.wait(timeout)):
            return False
        self._analises.shutdown(wait=True)
        return True

    def encerra(self):
        self._parar.set()
        if self._linha:
            self._linha.parar()
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
                    self._analises.submit(self._recebe, *chave)
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
        self._pronto.wait()
        self._analises.submit(self._recebe, fala.quem, fala.inicio_s, fala.texto)

    def _recebe(self, quem, inicio_s, texto):
        """A fala na sessão. Falhas do Claude a sessão já registra (etapas extrair e confirmar); o que
        mais escapar vai para a tela em vez de sumir na thread."""
        try:
            self.sessao.recebe_fala(quem, inicio_s, texto)
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


def _le_jsonl(arquivo):
    """Linhas JSON de um arquivo (vazio se não existe); uma última linha cortada por um travamento é
    ignorada."""
    if not Path(arquivo).exists():
        return []
    out = []
    for linha in Path(arquivo).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(linha))
        except json.JSONDecodeError:
            pass
    return out


def _publico(dados):
    """O conteúdo público do arquivo de dados: tudo menos o horário da última atualização."""
    return {**dados, "meta": {k: v for k, v in dados["meta"].items() if k != "atualizado_em"}}


def _difere_do_remoto(publicador, dados):
    """Se o ramo publicado no remoto já tem este conteúdo público (rodando de novo depois de um
    travamento), não há o que enviar. Na dúvida (sem rede, sem arquivo), envia."""
    import subprocess

    ref = f"refs/remotes/{publicador.remoto}/{publicador.ramo}"
    try:
        for args in (["fetch", "--quiet", "--no-tags", publicador.remoto, f"+refs/heads/{publicador.ramo}:{ref}"],
                     ["show", f"{ref}:{publicador.arquivo}"]):
            r = subprocess.run(["git", "-C", str(publicador.repo), *args], capture_output=True, timeout=60,
                               env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            if r.returncode:
                return True
        return _publico(json.loads(r.stdout.decode("utf-8"))) != _publico(dados)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return True


def _agora():
    return datetime.now(BRASILIA).isoformat(timespec="milliseconds")
