"""Os atalhos de duplo clique (iniciar/*.cmd -> scripts/iniciar.py): a noite publica no site de verdade com
o vocabulário da Globo (ou avisa na tela que ele falta); o ensaio publica numa cópia local do site, montada
sozinha numa pasta temporária e servida aqui, numa pasta de sessão própria que pode recomeçar do zero; os
dois abrem o navegador quando a tela responde."""
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from iniciar import (PROMPT_GLOBO, SiteDeEnsaio, apaga_copia, argumentos, do_zero, espera_e_abre,
                     prepara_site_de_ensaio)
from publicador import Publicador
from test_publicador import commits, dados, remoto  # noqa: F401 - remoto é fixture


def valor(args, opcao):
    return args[args.index(opcao) + 1]


def test_noite_publica_no_site_real_com_o_vocabulario_da_globo(tmp_path):
    (tmp_path / "vocabulario_globo.json").write_text('{"termos": ["Bonner"]}', encoding="utf-8")

    args = argumentos("noite", tmp_path, porta=8800)

    assert args[0] == "ao-vivo" and "--esperar-comecar" in args and "--publicar" in args
    assert "--repo" not in args and valor(args, "--destino") == "real"  # o repositório e o origin/main de sempre
    assert valor(args, "--vocabulario") == str(tmp_path / "vocabulario_globo.json")
    assert valor(args, "--prompt") == PROMPT_GLOBO and valor(args, "--porta") == "8800"
    assert "--aviso" not in args


def test_noite_sem_o_vocabulario_da_globo_roda_assim_mesmo_e_avisa_na_tela(tmp_path):
    args = argumentos("noite", tmp_path, porta=8765)

    assert "--vocabulario" not in args
    assert "vocabulario_globo.json" in valor(args, "--aviso")


def test_ensaio_publica_numa_copia_local_do_site_servida_aqui_sem_tocar_o_remoto(tmp_path, remoto):
    nu, repo = remoto
    antes = commits(nu)
    copia = tmp_path / "copia"

    publicar_em = prepara_site_de_ensaio(copia, raiz=repo)
    args = argumentos("ensaio", tmp_path / "dados", porta=8800, repo_ensaio=publicar_em,
                      site_url="http://127.0.0.1:8801/globo/")
    site = SiteDeEnsaio(copia, porta=0)
    publicador = Publicador(valor(args, "--repo"), valor(args, "--remoto"), valor(args, "--ramo"), intervalo=0)
    publicador.notifica(dados(2))
    assert publicador.encerra()

    with urllib.request.urlopen(f"http://127.0.0.1:{site.porta}/globo/data/painel.json") as r:
        assert json.loads(r.read()) == dados(2)  # o que foi enviado à cópia, não o que está na pasta
    assert valor(args, "--destino") == "ensaio" and valor(args, "--site-url") == "http://127.0.0.1:8801/globo/"
    assert valor(args, "--pasta") == str(tmp_path / "dados" / "ensaio" / "sessao_tela")
    assert commits(nu) == antes  # nada foi para o remoto de verdade
    assert prepara_site_de_ensaio(copia, raiz=repo) == publicar_em  # reaberto, segue a mesma cópia
    site.fecha()


def test_ensaio_do_zero_refaz_a_copia_do_site_mesmo_com_os_objetos_do_git_somente_leitura(tmp_path, remoto):
    _, repo = remoto
    copia = tmp_path / "copia"
    prepara_site_de_ensaio(copia, raiz=repo)
    (copia / "repo" / "sobra.txt").write_text("da cópia anterior", encoding="utf-8")
    somente_leitura = [f for f in copia.rglob("*") if f.is_file() and not os.access(f, os.W_OK)]

    apaga_copia(copia)
    publicar_em = prepara_site_de_ensaio(copia, raiz=repo)

    assert somente_leitura or os.name != "nt"  # no Windows, o git deixa os objetos somente leitura
    assert (publicar_em / ".git").exists() and not (publicar_em / "sobra.txt").exists()


def test_ensaio_do_zero_guarda_a_sessao_anterior_ao_lado_e_comeca_vazio(tmp_path):
    pasta = tmp_path / "sessao_tela"
    pasta.mkdir()
    (pasta / "eventos.jsonl").write_text('{"tipo": "inicio"}\n', encoding="utf-8")

    guardada = do_zero(pasta)

    assert not pasta.exists() or not any(pasta.iterdir())
    assert guardada.parent == tmp_path and (guardada / "eventos.jsonl").exists()
    assert do_zero(tmp_path / "nao_existe") is None


def test_abre_o_navegador_so_quando_a_tela_responde(tmp_path):
    abertos = []

    class Tela(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Tela)
    porta = servidor.server_address[1]
    servidor.server_close()  # a porta fica livre: a tela ainda não subiu
    th = threading.Thread(target=espera_e_abre, args=(porta, abertos.append), kwargs={"intervalo": 0.05}, daemon=True)
    th.start()
    time.sleep(0.4)
    assert abertos == []

    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Tela)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    th.join(10)
    servidor.shutdown()
    assert abertos == [f"http://127.0.0.1:{porta}/"]
