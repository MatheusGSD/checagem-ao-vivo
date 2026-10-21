"""O botão Começar (scripts/noite.py com a tela): a noite abre a tela num estado "pronto para começar",
carrega os modelos em segundo plano e só captura o áudio, atualiza a base, publica e chama o Claude depois
do Começar; "Retomar" segue uma sessão interrompida com um clique; "Encerrar a noite" faz a saída limpa
pela própria tela, que continua no ar dizendo que já pode fechar a janela.

Mesma linha falsa e mesmo dublê do Claude de test_noite."""
import json
import threading
from datetime import timedelta

from publicador import Publicador
from test_aprovacao import pede
from test_noite import Linhas, ate, falas_da_noite, indice, painel_no_remoto, registros  # noqa: F401 - fixtures
from test_publicador import commits, remoto  # noqa: F401 - remoto é fixture
from test_sessao import DESEMPREGO, FALA_DESEMPREGO, FALA_PIX, INICIO, PIX, Claude, Relogio

from noite import Noite


def url(noite, caminho=""):
    return f"http://127.0.0.1:{noite.tela.server_address[1]}{caminho}"


def estado(noite):
    codigo, corpo = pede(url(noite, "/estado"))
    assert codigo == 200
    return json.loads(corpo)


def acao(noite, nome, corpo=None):
    codigo, resposta = pede(url(noite, f"/acao/{nome}"), corpo or {})
    return codigo, json.loads(resposta)


def fase(noite):
    return estado(noite)["situacao"]["fase"]


class Base:
    """Atualização da base que só conta as chamadas."""

    def __init__(self):
        self.chamadas = 0

    def __call__(self, ao_erro):
        self.chamadas += 1
        return []


def test_a_tela_abre_pronta_para_comecar_e_nada_e_capturado_nem_enviado_antes_do_comecar(indice, tmp_path):
    carregou = threading.Event()
    linhas, base, claude = Linhas(falas_da_noite(), espera_parar=True), Base(), Claude({FALA_DESEMPREGO: [DESEMPREGO]})
    noite = Noite(tmp_path, indice, linhas, chamar=claude, relogio=Relogio(INICIO), porta=0, atualiza_base=base,
                  carregar=lambda: carregou.wait(30) and "modelos")

    noite.abre()
    assert fase(noite) == "carregando"
    codigo, resposta = acao(noite, "comecar")
    assert codigo == 400 and "carregando" in resposta["erro"]

    carregou.set()
    ate(lambda: fase(noite) == "pronto")
    assert estado(noite)["situacao"]["retomada"] is False
    assert (linhas.criadas, base.chamadas, claude.chamadas) == ([], 0, [])  # nada antes do Começar

    assert acao(noite, "comecar")[0] == 200
    ate(lambda: linhas.criadas and base.chamadas)
    assert fase(noite) == "rodando" and noite.modelos == "modelos"
    assert acao(noite, "comecar")[0] == 200  # clicar de novo não abre outra linha
    assert len(linhas.criadas) == 1
    noite.encerra()


def test_marcar_o_00_00_antes_do_comecar_e_recusado_com_o_motivo(indice, tmp_path):
    noite = Noite(tmp_path, indice, Linhas([], espera_parar=True), chamar=Claude({}), relogio=Relogio(INICIO), porta=0)
    noite.abre()
    ate(lambda: fase(noite) == "pronto")

    codigo, resposta = acao(noite, "inicio")

    assert codigo == 400 and "Começar" in resposta["erro"]
    assert noite.sessao.exporta()["meta"]["inicio_debate"] is None
    noite.encerra()


def test_modelos_que_nao_carregam_aparecem_na_tela_e_o_comecar_fica_travado(indice, tmp_path):
    def carregar():
        raise SystemExit("faltam os perfis de voz em data/vozes")

    noite = Noite(tmp_path, indice, Linhas([]), chamar=Claude({}), relogio=Relogio(INICIO), porta=0, carregar=carregar)
    noite.abre()
    ate(lambda: fase(noite) == "falhou")

    codigo, resposta = acao(noite, "comecar")
    assert codigo == 400 and "não carregaram" in resposta["erro"]
    assert "perfis de voz" in estado(noite)["situacao"]["erro_ao_carregar"]
    noite.encerra()


def test_rodar_de_novo_mostra_retomar_e_um_clique_segue_de_onde_parou(indice, tmp_path):
    claude = Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]})
    primeira = Noite(tmp_path, indice, Linhas(falas_da_noite()), chamar=claude,
                     relogio=Relogio(INICIO + timedelta(minutes=30)))
    primeira.marca_zero(INICIO)
    primeira.comeca()
    assert primeira.espera(timeout=60)
    primeira.encerra()
    fila = [p["trecho"] for p in primeira.sessao.fila()]

    linhas, de_novo = Linhas(falas_da_noite(), espera_parar=True), Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]})
    noite = Noite(tmp_path, indice, linhas, chamar=de_novo, relogio=Relogio(INICIO + timedelta(minutes=40)), porta=0)
    noite.abre()
    ate(lambda: fase(noite) == "pronto")
    antes = estado(noite)
    assert antes["situacao"]["retomada"] is True
    assert [p["trecho"] for p in antes["fila"]] == fila  # a fila de antes, já na tela antes do clique
    assert antes["meta"]["inicio_debate"] == INICIO.isoformat()

    assert acao(noite, "comecar")[0] == 200
    ate(lambda: linhas.criadas)
    assert fase(noite) == "rodando"
    assert [linha.desde for linha in linhas.criadas] == [905.0]  # depois da última fala entregue
    assert linhas.criadas[0].zeros == [INICIO]  # com o mesmo 00:00
    assert de_novo.chamadas == []
    noite.encerra()


def test_encerrar_pela_tela_faz_a_saida_limpa_e_a_tela_segue_no_ar_dizendo_que_acabou(indice, tmp_path, remoto):
    nu, repo = remoto
    linhas = Linhas(falas_da_noite(), espera_parar=True)
    noite = Noite(tmp_path / "sessao", indice, linhas, chamar=Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]}),
                  relogio=Relogio(INICIO + timedelta(minutes=30)), porta=0,
                  publicador=Publicador(repo, intervalo=3600, espera_erro=0.1))  # só o 1º e o último envio saem
    noite.marca_zero(INICIO)
    noite.abre()
    ate(lambda: fase(noite) == "pronto")
    acao(noite, "comecar")
    ate(lambda: len(noite.sessao.fila()) == 2)
    ate(lambda: not noite.publicador.situacao()["pendente"] or noite.publicador.situacao()["ultimo_envio_em"])
    envios = commits(nu)
    [proposta, _] = noite.sessao.fila()
    assert acao(noite, "aprovar", {"ligacao_id": proposta["id"]})[0] == 200

    assert acao(noite, "encerrar")[0] == 200
    ate(lambda: fase(noite) == "encerrada")

    final = estado(noite)["situacao"]
    assert final["encerramento"]["ultimo_envio_ok"] is True
    assert linhas.criadas[0]._parar.is_set()  # o áudio parou
    assert len(commits(nu)) == len(envios) + 1  # o último envio, com a aprovação
    assert [lig["id"] for lig in painel_no_remoto(nu)["ligacoes"]] == [proposta["id"]]
    codigo, resposta = acao(noite, "recusar", {"ligacao_id": noite.sessao.fila()[0]["id"]})
    assert codigo == 400 and "encerrada" in resposta["erro"]
    noite.encerra()  # o terminal fecha depois: não refaz nada
