"""Os atalhos de duplo clique da noite (pasta iniciar/ na raiz): montam o comando de scripts/noite.py, abrem
o navegador na tela do operador quando ela responde e deixam a janela mostrando o que acontece.

  noite   publica no site de verdade (origin/main, GitHub Pages), com o prompt e o vocabulário da Globo
          (DADOS/vocabulario_globo.json; sem ele, roda com o vocabulário da Band e avisa na tela). Sessão em
          DADOS/sessao_globo.
  ensaio  nada vai para o GitHub: publica numa cópia local do site, montada sozinha numa pasta temporária
          (%TEMP%/checagem-ao-vivo-ensaio) e servida em http://127.0.0.1:PORTA_SITE/globo/ para ver a página
          do leitor. Sessão própria em DADOS/ensaio/sessao_tela; --do-zero guarda a anterior ao lado
          (sessao_tela-AAAAMMDD-HHMMSS) e recomeça a cópia do site.

As duas abrem a tela em "Pronto para começar" (noite.py --esperar-comecar): nada é capturado nem enviado
ao Claude antes do botão Começar. Fechar a janela no meio deixa tudo retomável: abrir de novo mostra
"Retomar".

Uso:
  python -P scripts/iniciar.py noite  [--porta 8765] [--dados D] [--sem-navegador] [-- opções do noite.py]
  python -P scripts/iniciar.py ensaio [--do-zero] [--porta 8765] [--porta-site 8766] [--dados D] [--sem-navegador]
"""
import mimetypes
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comum import RAIZ, pasta_dados  # noqa: E402

PROMPT_GLOBO = "Debate presidencial na Globo, segundo turno. Lula e Flávio Bolsonaro."
COPIA_DO_SITE = Path(tempfile.gettempdir()) / "checagem-ao-vivo-ensaio"
AVISO_VOCABULARIO = ("Falta data/vocabulario_globo.json: o Whisper usa o vocabulário da Band e pode errar os nomes "
                     "dos mediadores da Globo. Crie o arquivo ({\"termos\": [...]}) antes da noite.")


def argumentos(modo, dados, porta=8765, repo_ensaio=None, site_url=None):
    """Os argumentos de noite.py para cada atalho."""
    dados = Path(dados)
    args = ["ao-vivo", "--esperar-comecar", "--publicar", "--porta", str(porta), "--prompt", PROMPT_GLOBO,
            "--dados", str(dados)]
    globo = dados / "vocabulario_globo.json"
    args += ["--vocabulario", str(globo)] if globo.exists() else ["--aviso", AVISO_VOCABULARIO]
    if modo == "noite":
        return args + ["--destino", "real"]
    return args + ["--destino", "ensaio", "--repo", str(repo_ensaio), "--remoto", "origin", "--ramo", "main",
                   "--site-url", site_url, "--pasta", str(dados / "ensaio" / "sessao_tela")]


# ---------------------------------------------------------------- cópia local do site (ensaio)

def _git(*args, **kw):
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode:
        raise RuntimeError(f"git {args[0] if args[0] != '-C' else args[2]}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def prepara_site_de_ensaio(copia=COPIA_DO_SITE, raiz=RAIZ):
    """Uma cópia descartável do site em `copia`: site.git (o "remoto", com o ramo main = o commit atual
    desta pasta) e repo/ (o clone em que o publicador grava e de onde empurra). Devolve repo/. Se a cópia
    já existe, segue com ela (o ensaio retomado vê o que já tinha publicado)."""
    copia = Path(copia)
    nu, repo = copia / "site.git", copia / "repo"
    if (nu / "HEAD").exists() and (repo / ".git").exists():
        return repo
    shutil.rmtree(copia, ignore_errors=True)
    copia.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "--bare", "-b", "main", str(nu))
    _git("--git-dir", str(nu), "fetch", "-q", "--no-tags", str(raiz), "+HEAD:refs/heads/main")
    _git("clone", "-q", str(nu), str(repo))
    return repo


class SiteDeEnsaio:
    """Serve a pasta docs/ do ramo main da cópia (o que o publicador já empurrou, não o que está gravado
    no clone) em 127.0.0.1:porta. porta=0 escolhe uma livre (em self.porta)."""

    def __init__(self, copia=COPIA_DO_SITE, porta=8766):
        nu = str(Path(copia) / "site.git")

        class Site(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                caminho = unquote(urlsplit(self.path).path)
                if caminho in ("", "/"):
                    self.send_response(302)
                    self.send_header("Location", "/globo/")
                    self.end_headers()
                    return
                partes = [p for p in caminho.split("/") if p]
                if any(p in (".", "..") or "\\" in p or ":" in p for p in partes):
                    return self._responde(404, b"")
                alvo = "/".join(partes) + ("/index.html" if caminho.endswith("/") else "")
                r = subprocess.run(["git", "--git-dir", nu, "cat-file", "blob", f"main:docs/{alvo}"], capture_output=True)
                if r.returncode:
                    return self._responde(404, "não existe nesta cópia do site".encode())
                tipo = mimetypes.guess_type(alvo)[0] or "application/octet-stream"
                self._responde(200, r.stdout, tipo + ("; charset=utf-8" if tipo.startswith(("text/", "application/json")) else ""))

            def _responde(self, codigo, corpo, tipo="text/plain; charset=utf-8"):
                self.send_response(codigo)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(corpo)

        self._servidor = ThreadingHTTPServer(("127.0.0.1", porta), Site)
        self._servidor.daemon_threads = True
        self.porta = self._servidor.server_address[1]
        threading.Thread(target=self._servidor.serve_forever, name="site-de-ensaio", daemon=True).start()

    def fecha(self):
        self._servidor.shutdown()
        self._servidor.server_close()


def do_zero(pasta):
    """Guarda a sessão de ensaio anterior ao lado (pasta-AAAAMMDD-HHMMSS) e devolve onde ficou; None se
    não havia nada."""
    pasta = Path(pasta)
    if not pasta.exists() or not any(pasta.iterdir()):
        return None
    guardada = pasta.with_name(f"{pasta.name}-{datetime.now():%Y%m%d-%H%M%S}")
    pasta.rename(guardada)
    return guardada


# ---------------------------------------------------------------- navegador

def porta_livre(porta):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", porta)) != 0


def espera_e_abre(porta, abrir=None, intervalo=0.5, limite_s=600):
    """Espera a tela responder em 127.0.0.1:porta e abre o navegador nela."""
    import webbrowser

    abrir = abrir or webbrowser.open
    url = f"http://127.0.0.1:{porta}/"
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        try:
            with urllib.request.urlopen(url + "estado", timeout=2):
                pass
        except OSError:
            time.sleep(intervalo)
            continue
        abrir(url)
        return True
    return False


# ---------------------------------------------------------------- linha de comando

def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modo", choices=["noite", "ensaio"])
    ap.add_argument("--porta", type=int, default=8765, help="porta da tela do operador (padrão 8765)")
    ap.add_argument("--porta-site", type=int, default=8766, help="ensaio: porta da cópia local do site (padrão 8766)")
    ap.add_argument("--dados", help="pasta de dados (padrão: CHECAGEM_DADOS ou data/ do checkout principal)")
    ap.add_argument("--do-zero", action="store_true", help="ensaio: guarda a sessão anterior e recomeça")
    ap.add_argument("--copia", default=str(COPIA_DO_SITE), help="ensaio: pasta da cópia local do site")
    ap.add_argument("--sem-navegador", action="store_true", help="não abre o navegador")
    a, resto = ap.parse_known_args(argv)
    resto = [x for x in resto if x != "--"]
    dados = pasta_dados(a.dados)

    if not porta_livre(a.porta):
        print(f"A porta {a.porta} já está em uso: o programa já está aberto noutra janela? "
              + ("Abrindo a tela que está lá. " if not a.sem_navegador else "")
              + "Se não for ela, feche a outra janela e abra de novo.", flush=True)
        if not a.sem_navegador:
            espera_e_abre(a.porta, limite_s=5)
        return 1

    if a.modo == "noite":
        print("NOITE DO DEBATE: publica no SITE DE VERDADE (origin/main, GitHub Pages).", flush=True)
        args = argumentos("noite", dados, a.porta)
    else:
        pasta = dados / "ensaio" / "sessao_tela"
        if a.do_zero:
            if guardada := do_zero(pasta):
                print(f"ensaio do zero: a sessão anterior ficou em {guardada}", flush=True)
            shutil.rmtree(a.copia, ignore_errors=True)
        if not porta_livre(a.porta_site):
            sys.exit(f"A porta {a.porta_site} (cópia local do site) já está em uso. Feche a outra janela do ensaio.")
        repo = prepara_site_de_ensaio(a.copia)
        site = SiteDeEnsaio(a.copia, a.porta_site)
        url_site = f"http://127.0.0.1:{site.porta}/globo/"
        print(f"ENSAIO: nada vai para o GitHub. A página do leitor (cópia local) fica em {url_site}", flush=True)
        args = argumentos("ensaio", dados, a.porta, repo_ensaio=repo, site_url=url_site)
    if "--aviso" in args:
        print("ATENÇÃO: " + args[args.index("--aviso") + 1], flush=True)
    if not a.sem_navegador:
        threading.Thread(target=espera_e_abre, args=(a.porta,), daemon=True).start()
    print("A tela do operador abre no navegador em instantes: http://127.0.0.1:%d/" % a.porta, flush=True)

    import noite

    noite.main(args + resto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
