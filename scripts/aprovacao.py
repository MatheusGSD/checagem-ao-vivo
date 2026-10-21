"""Tela de aprovação: página local, servida pela máquina do operador, que mostra a fila e o estado da
sessão de checagem em tempo real e envia as ações do operador (aprovar com selo, editar o trecho e a
reformulação, recusar, despublicar com motivo, corrigir rótulo, marcar o 00:00).

Só escuta em 127.0.0.1. A página (scripts/aprovacao.html) busca GET /estado a cada ~1,5 s e manda
cada ação como POST /acao/<nome> com corpo JSON.

Uso:
  python -P scripts/aprovacao.py [--dados DADOS] [--pasta PASTA] [--porta 8765] [--sem-retomar]
                                 [--publicar [--repo PASTA] [--remoto origin] [--ramo main] [--intervalo 60]]

  DADOS: --dados, CHECAGEM_DADOS ou data/ do checkout principal (base em DADOS/checagens, limiar em
         DADOS/avaliacao/limiar.json).
  PASTA: pasta da sessão, com o registro eventos.jsonl (padrão DADOS/sessao_globo). Se o registro
         existe, a sessão é reconstruída a partir dele; análises interrompidas por um travamento são
         terminadas em segundo plano (chamam o Claude), a menos que se passe --sem-retomar.
  --publicar: liga o publicador (scripts/publicador.py): cada mudança pública vai para
         docs/globo/data/painel.json do repositório (padrão: este) e, em commits agrupados, para o
         ramo do remoto (padrão origin/main, servido pelo GitHub Pages). Falhas aparecem na faixa de
         erros da tela. Ctrl+C faz o último envio antes de sair.

Em código (integração ao vivo): `servidor = serve(sessao, porta)` sobe a tela numa thread e
devolve o servidor (servidor.server_address, servidor.shutdown()).
"""
import json
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sessao import AcaoInvalida  # noqa: E402

PAGINA = Path(__file__).resolve().parent / "aprovacao.html"
SITE = "https://matheusgsd.github.io/debate-lula-flavio/globo/"  # a página pública, servida pelo Pages
HOST = "127.0.0.1"
PORTA = 8765


# painel de erros da tela: etapa registrada (Sessao.erros) -> quadro
QUADROS_DE_ERRO = [("Transcrição e áudio", ("transcricao", "audio")), ("API do Claude", ("extrair", "confirmar")),
                   ("API do Google (base)", ("base",)), ("Publicação", ("publicar",))]


def painel_de_erros(erros):
    """Um quadro por etapa da noite (mais "Outros", se houver), com o número de erros e o último."""
    quadros = [(nome, etapas, [e for e in erros if e["etapa"] in etapas]) for nome, etapas in QUADROS_DE_ERRO]
    conhecidas = {etapa for _, etapas in QUADROS_DE_ERRO for etapa in etapas}
    if outros := [e for e in erros if e["etapa"] not in conhecidas]:
        quadros.append(("Outros", (), outros))
    return [{"nome": nome, "n": len(lista), "ultimo": lista[-1] if lista else None} for nome, _, lista in quadros]


AVISO_LIMIAR = "publicação automática desligada: "


def estado(sessao, situacao=None):
    """O que a tela mostra: fila, publicadas, correções, recusadas, sem par, descartes, erros (um quadro
    por etapa), o andamento (cada fala e o que saiu dela, Sessao.andamento), as chamadas ao Claude em
    curso, as contagens por candidato, o tamanho da base, a publicação automática (limiar e, desligada,
    o motivo: um ajuste da noite, não um erro) e a situação da noite (situacao(): dicionário curto, da
    integração ao vivo)."""
    versao = len(sessao.eventos)  # lida antes: um evento no meio só faz a próxima busca redesenhar
    dados = sessao.exporta()
    todos = sessao.erros()
    erros = [e for e in todos if e["etapa"] != "limiar"]
    motivo = next((e["mensagem"].removeprefix(AVISO_LIMIAR) for e in reversed(todos) if e["etapa"] == "limiar"), None)
    limiar = dados["meta"]["limiar_automatico"]
    return {
        "versao": versao,
        "agora": dados["meta"]["atualizado_em"],
        "meta": {k: dados["meta"][k] for k in ("titulo", "debate", "inicio_debate", "limiar_automatico")},
        "fila": sessao.fila(),
        "publicadas": dados["ligacoes"],
        "correcoes": dados["correcoes"],
        "sem_par": dados["sem_par"],
        "recusadas": sessao.recusadas(),
        "descartados": sessao.descartados(),
        "erros": erros,
        "painel_erros": painel_de_erros(erros),
        "automatica": {"limiar": limiar, "motivo": motivo if limiar is None else None},
        "base": {"checagens": len(sessao.indice.registros)},
        "site": SITE,
        "andamento": sessao.andamento(),
        "chamadas_claude_em_curso": sessao.chamadas_em_curso(),
        "contagens": sessao.contagens(),
        "situacao": situacao() if situacao else None,
    }


def _acoes(sessao):
    return {
        "aprovar": lambda c: sessao.aprova(c["ligacao_id"], selos=c.get("selos"), manter=c.get("manter")),
        "recusar": lambda c: sessao.recusa(c["ligacao_id"], c.get("motivo")),
        "editar": lambda c: sessao.edita(c["ligacao_id"], trecho=c.get("trecho"), reformulacao=c.get("reformulacao")),
        "despublicar": lambda c: sessao.despublica(c["ligacao_id"], c.get("motivo")),
        "rotulo": lambda c: sessao.corrige_rotulo(c["ligacao_id"], c["url"], c.get("rotulo")),
        "inicio": lambda c: sessao.marca_inicio(c.get("instante")),
    }


def _handler(sessao, situacao=None):
    acoes = _acoes(sessao)

    class Tela(BaseHTTPRequestHandler):
        server_version = "TelaDeAprovacao"

        def log_message(self, formato, *args):  # só ações e erros vão para o terminal
            if self.command == "POST" or (args and str(args[1])[:1] in "45"):
                sys.stderr.write(f"[tela] {self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")

        def _origem_local(self):
            """Só a própria tela: barra outro site aberto no navegador do operador (Origin) e DNS
            rebinding (Host)."""
            porta = self.server.server_address[1]
            locais = {f"127.0.0.1:{porta}", f"localhost:{porta}"}
            if self.headers.get("Host") not in locais:
                return False
            origem = self.headers.get("Origin")
            return origem is None or urlsplit(origem).netloc in locais

        def _responde(self, codigo, corpo, tipo="application/json; charset=utf-8"):
            dados = corpo if isinstance(corpo, bytes) else json.dumps(corpo, ensure_ascii=False).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(dados)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(dados)

        def do_GET(self):
            if not self._origem_local():
                return self._responde(403, {"erro": "só a tela local"})
            caminho = urlsplit(self.path).path
            if caminho == "/":
                return self._responde(200, PAGINA.read_bytes(), "text/html; charset=utf-8")
            if caminho == "/estado":
                return self._responde(200, estado(sessao, situacao))
            if caminho == "/favicon.ico":
                return self._responde(204, b"", "image/x-icon")
            self._responde(404, {"erro": "não existe"})

        def do_POST(self):
            if not self._origem_local():
                return self._responde(403, {"erro": "só a tela local"})
            nome = urlsplit(self.path).path.removeprefix("/acao/")
            if nome not in acoes:
                return self._responde(404, {"erro": f"ação desconhecida: {nome}"})
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._responde(415, {"erro": "corpo deve ser JSON"})
            try:
                corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                acoes[nome](corpo)
            except (AcaoInvalida, KeyError, TypeError, ValueError) as e:
                return self._responde(400, {"erro": str(e) if not isinstance(e, KeyError) else f"falta {e}"})
            self._responde(200, {"ok": True, "versao": len(sessao.eventos)})

    return Tela


def serve(sessao, porta=PORTA, situacao=None):
    """Sobe a tela numa thread e devolve o servidor. porta=0 escolhe uma livre. situacao(): o que mostrar
    da noite no cabeçalho (falas, última fala, base...)."""
    servidor = ThreadingHTTPServer((HOST, porta), _handler(sessao, situacao))
    servidor.daemon_threads = True
    threading.Thread(target=servidor.serve_forever, name="tela-de-aprovacao", daemon=True).start()
    return servidor


def main():
    import argparse

    from base_checagens import Indice, pasta_base
    from comum import pasta_dados
    from sessao import Sessao, le_limiar

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dados", help="pasta de dados (padrão: CHECAGEM_DADOS ou data/ do checkout principal)")
    ap.add_argument("--pasta", help="pasta da sessão, com eventos.jsonl (padrão: DADOS/sessao_globo)")
    ap.add_argument("--porta", type=int, default=PORTA)
    ap.add_argument("--sem-retomar", action="store_true", help="não termina análises interrompidas")
    ap.add_argument("--publicar", action="store_true", help="envia o painel ao GitHub Pages (publicador.py)")
    ap.add_argument("--repo", help="repositório do site (padrão: este)")
    ap.add_argument("--remoto", default="origin", help="remoto do site (padrão: origin)")
    ap.add_argument("--ramo", default="main", help="ramo publicado pelo Pages (padrão: main)")
    ap.add_argument("--intervalo", type=float, default=60, help="segundos mínimos entre pushes (padrão: 60)")
    a = ap.parse_args()
    dados = pasta_dados(a.dados)
    pasta = Path(a.pasta) if a.pasta else dados / "sessao_globo"
    limiar, sem_limiar = le_limiar(dados)
    indice = Indice.da_pasta(pasta_base(dados))
    publicador = None
    if a.publicar:
        from publicador import RAIZ as REPO_PADRAO
        from publicador import Publicador

        publicador = Publicador(
            a.repo or REPO_PADRAO, a.remoto, a.ramo, intervalo=a.intervalo,
            ao_erro=lambda m: sessao.registra_erro("publicar", m),
            ao_publicar=lambda c: print(f"[publicador] {datetime.now():%H:%M:%S} {c[:8]} → {a.remoto}/{a.ramo}",
                                        flush=True))
    sessao = Sessao.abre(pasta, indice, meta={"limiar_automatico": limiar},
                         ao_mudar_publicado=publicador.notifica if publicador else None)
    if publicador:
        publicador.notifica(sessao.exporta())  # a sessão não avisa o estado inicial
    if sem_limiar:  # o operador vê na tela por que nada sai sozinho
        sessao.registra_erro("limiar", f"publicação automática desligada: {sem_limiar}", uma_vez=True)
    print(f"base: {len(indice.registros)} checagens · registro: {pasta / 'eventos.jsonl'} "
          f"({len(sessao.eventos)} eventos) · publicação automática: "
          + (f"nota ≥ {limiar} depois de 10 min" if limiar is not None else f"desligada ({sem_limiar})"), flush=True)
    if publicador:
        print(f"publicador: {publicador.repo / publicador.arquivo} → {a.remoto}/{a.ramo}, "
              f"no máximo um push a cada {a.intervalo:g} s", flush=True)
    if not a.sem_retomar:
        threading.Thread(target=sessao.retoma_analises, daemon=True).start()
    servidor = serve(sessao, a.porta, (lambda: {"publicacao": publicador.situacao()}) if publicador else None)
    print(f"tela de aprovação: http://127.0.0.1:{servidor.server_address[1]}/  (Ctrl+C encerra)", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        servidor.shutdown()
        if publicador:
            print("enviando o último painel...", flush=True)
            if not publicador.encerra():
                print("ATENÇÃO: o último painel não foi enviado (ver erros acima)", flush=True)
        print(f"encerrada às {datetime.now():%H:%M:%S}")


if __name__ == "__main__":
    main()
