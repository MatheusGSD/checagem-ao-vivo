"""Publicador: leva cada mudança pública da sessão de checagem ao GitHub Pages.

A cada aviso (`notifica(dados)`, o gancho `ao_mudar_publicado` da sessão) o arquivo de dados da página
(docs/globo/data/painel.json) é regravado na pasta do repositório e, numa thread própria, vira um commit
no ramo publicado do remoto (padrão: origin/main, que o Pages serve de /docs). Pushes são agrupados:
no máximo um a cada `intervalo` segundos (60), sempre com o conteúdo mais recente.

O commit é montado sobre a ponta do ramo no remoto, buscada a cada envio, e só troca o arquivo de dados
(índice temporário; nada no ramo em que a pasta está, no índice ou nos outros arquivos dela muda). Se o
remoto andou no meio (push recusado), busca de novo e refaz o commit sobre a nova ponta. Falhas (rede,
credencial, recusa) vão para `ao_erro(mensagem)` (a faixa de erros da tela de aprovação) e são
tentadas de novo a cada `espera_erro` segundos, sem perder a mudança. `encerra()` faz o último envio.

Uso:
  python -P scripts/publicador.py verifica [--repo PASTA] [--remoto origin] [--ramo main]
    confere, antes da noite, que dá para buscar e empurrar para o remoto (credenciais prontas).

Em código: p = Publicador(repo, ao_erro=...); Sessao(..., ao_mudar_publicado=p.notifica);
p.notifica(sessao.exporta()) ao começar; p.encerra() ao sair. Ver `aprovacao.py --publicar`.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comum import BRASILIA  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO = "docs/globo/data/painel.json"
INTERVALO = 60.0
ESPERA_ERRO = 15.0
TENTATIVAS_SE_O_REMOTO_ANDOU = 3
AVISO_REPETIDO_S = 300  # a mesma falha volta à tela no máximo a cada 5 min


class ErroGit(RuntimeError):
    pass


class Publicador:
    def __init__(self, repo=RAIZ, remoto="origin", ramo="main", arquivo=ARQUIVO, intervalo=INTERVALO,
                 espera_erro=ESPERA_ERRO, ao_erro=None, ao_publicar=None, timeout_git=120):
        """repo: pasta do repositório (worktree serve). ao_erro(mensagem): falha de envio ou gravação,
        e o aviso de que voltou a funcionar. ao_publicar(commit): cada envio aceito pelo remoto."""
        self.repo = Path(repo)
        self.remoto, self.ramo, self.arquivo = remoto, ramo, arquivo
        self.intervalo, self.espera_erro, self.timeout_git = intervalo, espera_erro, timeout_git
        self._ao_erro = ao_erro or (lambda m: sys.stderr.write(f"[publicador] {m}\n"))
        self._ao_publicar = ao_publicar
        self._cond = threading.Condition()
        self._dados, self._versao = None, 0
        self._gravada = self._publicada = 0
        self._proximo = 0.0  # instante (monotonic) a partir do qual pode haver outro push
        self._falhas, self._ultimo_aviso = 0, None
        self._envio = {"ultimo_envio_em": None, "ultimo_commit": None, "ultimo_erro": None, "ultimo_erro_em": None}
        self._parar = False
        self._thread = threading.Thread(target=self._roda, name="publicador", daemon=True)
        self._thread.start()

    # ---------- interface ----------

    def notifica(self, dados):
        """Novo conteúdo público. Rápido e sem git: pode rodar com a sessão travada."""
        with self._cond:
            self._dados, self._versao = dados, self._versao + 1
            self._cond.notify()

    def situacao(self):
        """Para a tela: quando o último envio chegou ao remoto (e o commit), se há mudança esperando
        envio, quantas tentativas seguidas falharam e a última falha (None depois que volta a funcionar)."""
        with self._cond:
            return {**self._envio, "pendente": self._versao > self._publicada, "falhas": self._falhas}

    def difere_do_remoto(self, dados, chave=lambda d: d):
        """Se o ramo publicado no remoto ainda não tem este conteúdo (comparado por `chave(dados)`, ex.
        sem o horário da última atualização). Rodando de novo depois de um travamento, o remoto já pode
        ter tudo, e não há o que enviar. Na dúvida (sem rede, sem o arquivo no remoto), difere."""
        rastreio = f"refs/remotes/{self.remoto}/{self.ramo}"
        try:
            self._git_ok("fetch", "--quiet", "--no-tags", self.remoto, f"+refs/heads/{self.ramo}:{rastreio}")
            return chave(json.loads(self._git_ok("show", f"{rastreio}:{self.arquivo}"))) != chave(dados)
        except (ErroGit, ValueError, KeyError, TypeError):
            return True

    def encerra(self, timeout=60):
        """Envia o que falta (sem esperar o intervalo; uma tentativa) e para a thread. True se nada
        ficou sem publicar."""
        with self._cond:
            self._parar = True
            self._cond.notify()
        self._thread.join(timeout)
        return not self._thread.is_alive() and self._publicada == self._versao

    # ---------- thread ----------

    def _roda(self):
        while True:
            with self._cond:
                while True:
                    agora = time.monotonic()
                    if self._versao > self._gravada:
                        break
                    if self._versao > self._publicada and (self._parar or agora >= self._proximo):
                        break
                    if self._parar:
                        return
                    self._cond.wait(None if self._versao == self._publicada else self._proximo - agora)
                versao, dados, parar = self._versao, self._dados, self._parar
            conteudo = (json.dumps(dados, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if versao > self._gravada:
                self._grava(conteudo)
                self._gravada = versao
            if not (parar or time.monotonic() >= self._proximo):
                continue
            try:
                commit = self._envia(conteudo, dados)
            except Exception as e:  # noqa: BLE001 - a thread não pode morrer: sem ela nada mais vai ao site
                self._falhou(str(e) if isinstance(e, ErroGit) else f"{type(e).__name__}: {e}")
                if parar:
                    return
                self._proximo = time.monotonic() + self.espera_erro
                continue
            with self._cond:
                self._publicada = versao
                self._envio.update(ultimo_envio_em=_agora(), ultimo_erro=None, ultimo_erro_em=None,
                                   **({"ultimo_commit": commit} if commit else {}))
                falhas, self._falhas = self._falhas, 0
            self._proximo = time.monotonic() + self.intervalo
            if falhas:
                self._ao_erro(f"publicação voltou a funcionar às {datetime.now(BRASILIA):%H:%M:%S}, "
                              f"depois de {falhas} tentativa(s) com falha")
                self._ultimo_aviso = None
            if commit and self._ao_publicar:
                self._ao_publicar(commit)

    def _falhou(self, mensagem):
        with self._cond:
            self._falhas += 1
            self._envio.update(ultimo_erro=mensagem, ultimo_erro_em=_agora())
        agora = time.monotonic()
        aviso = (mensagem, agora)
        if (self._ultimo_aviso is None or self._ultimo_aviso[0] != mensagem
                or agora - self._ultimo_aviso[1] >= AVISO_REPETIDO_S):
            self._ultimo_aviso = aviso
            self._ao_erro(f"falha ao publicar (tentativa {self._falhas}; tenta de novo em "
                          f"{self.espera_erro:.0f} s): {mensagem}")

    def _grava(self, conteudo):
        destino = self.repo / self.arquivo
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            temporario = destino.with_name(destino.name + ".tmp")
            temporario.write_bytes(conteudo)
            for _ in range(20):  # no Windows, quem está lendo o arquivo trava a troca por um instante
                try:
                    os.replace(temporario, destino)
                    return
                except PermissionError:
                    time.sleep(0.05)
            os.replace(temporario, destino)
        except OSError as e:
            self._ao_erro(f"não gravou {destino} (o envio segue): {e}")

    # ---------- git ----------

    def _git(self, *args, entrada=None, env=None):
        try:
            r = subprocess.run(["git", "-C", str(self.repo), *args], input=entrada, capture_output=True,
                               timeout=self.timeout_git, env=env or self._env())
        except subprocess.TimeoutExpired:
            raise ErroGit(f"git {args[0]}: sem resposta em {self.timeout_git} s") from None
        except OSError as e:
            raise ErroGit(f"git {args[0]}: {e}") from None
        return r.returncode, r.stdout.decode("utf-8", "replace").strip(), r.stderr.decode("utf-8", "replace").strip()

    def _git_ok(self, *args, **kw):
        codigo, saida, erro = self._git(*args, **kw)
        if codigo:
            raise ErroGit(f"git {args[0]}: {_ultimas_linhas(erro or saida)}")
        return saida

    @staticmethod
    def _env(**extra):
        # sem perguntas no terminal: credencial faltando vira erro na tela, e não uma espera sem fim
        return {**os.environ, "GIT_TERMINAL_PROMPT": "0", **extra}

    def _envia(self, conteudo, dados):
        """Commit com o conteúdo sobre a ponta do ramo no remoto, e push. Devolve o commit, ou None se o
        remoto já tem esse conteúdo."""
        rastreio = f"refs/remotes/{self.remoto}/{self.ramo}"
        blob = self._git_ok("hash-object", "-w", "--stdin", entrada=conteudo)
        for _ in range(TENTATIVAS_SE_O_REMOTO_ANDOU):
            self._git_ok("fetch", "--quiet", "--no-tags", self.remoto, f"+refs/heads/{self.ramo}:{rastreio}")
            base = self._git_ok("rev-parse", "--verify", f"{rastreio}^{{commit}}")
            codigo, atual, _ = self._git("rev-parse", "--verify", "--quiet", f"{base}:{self.arquivo}")
            if codigo == 0 and atual == blob:
                return None
            commit = self._commit(base, blob, dados)
            codigo, saida, erro = self._git("push", self.remoto, f"{commit}:refs/heads/{self.ramo}")
            if codigo == 0:
                return commit
            if not _remoto_andou(saida + "\n" + erro):
                raise ErroGit(f"git push: {_ultimas_linhas(erro or saida)}")
        raise ErroGit(f"o ramo {self.ramo} mudou no remoto durante {TENTATIVAS_SE_O_REMOTO_ANDOU} envios seguidos")

    def _commit(self, base, blob, dados):
        with tempfile.TemporaryDirectory(prefix="publicador-") as pasta:
            env = self._env(GIT_INDEX_FILE=str(Path(pasta) / "index"))
            self._git_ok("read-tree", base, env=env)
            self._git_ok("update-index", "--add", "--cacheinfo", f"100644,{blob},{self.arquivo}", env=env)
            arvore = self._git_ok("write-tree", env=env)
        return self._git_ok("commit-tree", arvore, "-p", base, "-m", mensagem(dados))


def mensagem(dados):
    """Mensagem padrão dos commits do publicador."""
    n = lambda chave: len((dados or {}).get(chave) or [])  # noqa: E731
    return (f"Checagem ao vivo: painel atualizado às {datetime.now(BRASILIA):%H:%M:%S} "
            f"({n('ligacoes')} ligações, {n('correcoes')} correções)")


def _agora():
    return datetime.now(BRASILIA).isoformat(timespec="seconds")


def _remoto_andou(erro):
    return any(s in erro for s in ("non-fast-forward", "fetch first", "[rejected]", "stale info"))


def _ultimas_linhas(texto):
    """As últimas linhas de uma mensagem do git (sem as dicas), numa linha só, para a tela."""
    linhas = [l for l in texto.splitlines() if l.strip() and not l.startswith("hint:")]
    return " | ".join(linhas[-3:]) or "erro sem mensagem"


def verifica(repo=RAIZ, remoto="origin", ramo="main"):
    """Busca o ramo e simula um push (git push --dry-run), com perguntas de credencial liberadas."""
    for args in (["fetch", "--no-tags", remoto, f"+refs/heads/{ramo}:refs/remotes/{remoto}/{ramo}"],
                 ["push", "--dry-run", remoto, f"refs/remotes/{remoto}/{ramo}:refs/heads/{ramo}"]):
        print("$ git " + " ".join(args), flush=True)
        if subprocess.run(["git", "-C", str(repo), *args]).returncode:
            print("FALHOU: o publicador não vai conseguir enviar", flush=True)
            return False
    print(f"ok: {remoto}/{ramo} aceita push a partir de {repo}", flush=True)
    return True


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("comando", choices=["verifica"])
    ap.add_argument("--repo", default=str(RAIZ), help="pasta do repositório (padrão: este)")
    ap.add_argument("--remoto", default="origin")
    ap.add_argument("--ramo", default="main")
    a = ap.parse_args()
    sys.exit(0 if verifica(a.repo, a.remoto, a.ramo) else 1)


if __name__ == "__main__":
    main()
