"""Tela de aprovação: as ações chegam à sessão por HTTP, e só a partir da própria máquina e da própria tela."""
import json
import threading
import urllib.error
import urllib.request
from datetime import timedelta

import pytest

from aprovacao import serve
from base_checagens import Indice
from sessao import Sessao
from test_sessao import (FALA_DESEMPREGO, FALA_GASOLINA, FALA_MEDIACAO, FALA_PIX, FIXTURES, INICIO, PIX, PIX_ESTADAO, Claude, Relogio,
                         afirmacao, pedido)


@pytest.fixture(scope="module")
def indice():
    return Indice(json.loads((FIXTURES / "base_sessao.json").read_text(encoding="utf-8"))["registros"])


@pytest.fixture
def tela(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=Relogio(INICIO + timedelta(minutes=30)))
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)
    servidor = serve(s, porta=0)
    yield s, f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()


def pede(url, corpo=None, **cabecalhos):
    dados = None if corpo is None else json.dumps(corpo).encode()
    if dados is not None:
        cabecalhos.setdefault("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, dados, cabecalhos)) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_tela_mostra_a_fila_e_aprovar_com_selo_publica(tela):
    s, base = tela
    codigo, pagina = pede(base + "/")
    assert codigo == 200 and b"Aprova" in pagina
    [proposta] = json.loads(pede(base + "/estado")[1])["fila"]
    assert [c["url"] for c in proposta["checagens"]][0] == PIX_ESTADAO

    codigo, _ = pede(base + "/acao/aprovar", {"ligacao_id": proposta["id"], "selos": {PIX_ESTADAO: "Falso"}})

    assert codigo == 200
    estado = json.loads(pede(base + "/estado")[1])
    assert estado["fila"] == []
    [lig] = estado["publicadas"]
    assert {c["url"]: c["rotulo"] for c in lig["checagens"]}[PIX_ESTADAO] == "Falso"
    assert s.exporta()["ligacoes"] == estado["publicadas"]


def test_acao_que_nao_vale_volta_com_o_motivo_e_nada_muda(tela):
    s, base = tela
    ident = s.fila()[0]["id"]

    codigo, corpo = pede(base + "/acao/despublicar", {"ligacao_id": ident, "motivo": "teste"})

    assert codigo == 400 and "não está publicada" in json.loads(corpo)["erro"]
    assert len(s.fila()) == 1


def test_outro_site_aberto_no_navegador_nao_age_na_sessao(tela):
    s, base = tela
    ident = s.fila()[0]["id"]

    codigo, _ = pede(base + "/acao/recusar", {"ligacao_id": ident}, Origin="https://exemplo.com")
    assert codigo == 403
    codigo, _ = pede(base + "/acao/recusar", {"ligacao_id": ident}, Host="exemplo.com")
    assert codigo == 403

    assert len(s.fila()) == 1


def test_editar_pela_tela_troca_a_reformulacao_e_trecho_que_nao_bate_volta_com_o_motivo(tela):
    s, base = tela
    ident = s.fila()[0]["id"]

    codigo, _ = pede(base + "/acao/editar", {"ligacao_id": ident, "reformulacao": "Flávio diz que o Pix é do governo Bolsonaro."})
    assert codigo == 200
    assert json.loads(pede(base + "/estado")[1])["fila"][0]["reformulacao"] == "Flávio diz que o Pix é do governo Bolsonaro."

    codigo, corpo = pede(base + "/acao/editar", {"ligacao_id": ident, "trecho": "frase que ninguém disse"})
    assert codigo == 400 and "letra a letra" in json.loads(corpo)["erro"]


# ---------- a noite ao vivo na tela: cada fala, o que saiu dela e se o pipeline está andando ----------

GASOLINA = afirmacao("a gasolina caiu trinta por cento no posto em um ano",
                     "O preço da gasolina caiu 30% em um ano no governo Bolsonaro.", "numero")
GASOLINA_30 = afirmacao("a gasolina caiu 30% no posto", "A gasolina caiu 30%.", "numero")  # não é literal


def andamento(base):
    return json.loads(pede(base + "/estado")[1])["andamento"]


def test_tela_mostra_cada_fala_com_quem_falou_e_o_que_aconteceu_com_cada_afirmacao(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX], FALA_GASOLINA: [GASOLINA, GASOLINA_30]}),
               relogio=Relogio(INICIO + timedelta(minutes=30)))
    s.recebe_fala("Mediação", 1790.0, FALA_MEDIACAO, atraso_s=12.5)
    s.recebe_fala("Flávio", 1800.0, FALA_PIX, atraso_s=18.3)
    s.recebe_fala("Flávio", 1850.0, FALA_GASOLINA)
    servidor = serve(s, porta=0)
    base = f"http://127.0.0.1:{servidor.server_address[1]}"
    try:
        gasolina, pix, mediacao = andamento(base)  # a mais recente primeiro

        assert (mediacao["quem"], mediacao["texto"], mediacao["atraso_s"]) == ("Mediação", FALA_MEDIACAO, 12.5)
        assert mediacao["afirmacoes"] == [] and mediacao["analise"] == "mediacao"
        assert (pix["quem"], pix["minutagem_s"], pix["atraso_s"], pix["analise"]) == ("Flávio", 1800.0, 18.3, "feita")
        [a] = pix["afirmacoes"]
        assert (a["trecho"], a["reformulacao"], a["tipo"], a["situacao"]) == (
            PIX["trecho"], PIX["reformulacao"], "perene", "fila")
        [sem_par] = gasolina["afirmacoes"]
        assert (sem_par["trecho"], sem_par["situacao"]) == (GASOLINA["trecho"], "sem_par")
        assert sem_par["candidatas"] and all(c["agencia"] and c["url"] for c in sem_par["candidatas"])
        assert "nenhuma" in sem_par["veredito"]
        assert [d["trecho"] for d in gasolina["descartadas"]] == [GASOLINA_30["trecho"]]

        s.aprova(a["ligacao_id"])

        assert andamento(base)[1]["afirmacoes"][0]["situacao"] == "publicada"
    finally:
        servidor.shutdown()


def test_tela_mostra_a_fala_ainda_em_analise_e_as_chamadas_ao_claude_em_curso(indice):
    solta, chegou = threading.Event(), threading.Event()
    dublê = Claude({FALA_PIX: [PIX]})

    def chamar(etapa, prompt, conteudo, schema):
        if pedido(schema) == "extracao":
            chegou.set()
            solta.wait(10)
        return dublê(etapa, prompt, conteudo, schema)

    s = Sessao(indice, chamar=chamar, relogio=Relogio(INICIO + timedelta(minutes=30)))
    servidor = serve(s, porta=0)
    base = f"http://127.0.0.1:{servidor.server_address[1]}"
    analise = threading.Thread(target=s.recebe_fala, args=("Flávio", 1800.0, FALA_PIX))
    try:
        analise.start()
        assert chegou.wait(10)
        estado = json.loads(pede(base + "/estado")[1])
        assert estado["chamadas_claude_em_curso"] == 1
        [fala] = estado["andamento"]
        assert (fala["analise"], fala["afirmacoes"]) == ("analisando", [])
    finally:
        solta.set()
        analise.join(10)
        servidor.shutdown()
    assert s.fila()


# ---------- o que a tela do operador resume da noite ----------

def test_tela_conta_falas_afirmacoes_e_ligacoes_de_cada_candidato(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX], FALA_GASOLINA: [GASOLINA, GASOLINA_30]}),
               relogio=Relogio(INICIO + timedelta(minutes=30)))
    s.recebe_fala("Mediação", 1790.0, FALA_MEDIACAO)
    s.recebe_fala("Lula", 1795.0, FALA_DESEMPREGO)  # nenhuma afirmação checável
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)
    s.recebe_fala("Flávio", 1850.0, FALA_GASOLINA)  # uma sem par e uma descartada (não literal)
    servidor = serve(s, porta=0)
    base = f"http://127.0.0.1:{servidor.server_address[1]}"
    try:
        contagens = json.loads(pede(base + "/estado")[1])["contagens"]
        assert contagens == {"Lula": {"falas": 1, "afirmacoes": 0, "ligacoes": 0, "publicadas": 0},
                             "Flávio": {"falas": 2, "afirmacoes": 2, "ligacoes": 1, "publicadas": 0}}

        s.aprova(s.fila()[0]["id"])

        assert json.loads(pede(base + "/estado")[1])["contagens"]["Flávio"]["publicadas"] == 1
    finally:
        servidor.shutdown()


def test_publicacao_automatica_desligada_aparece_como_ajuste_e_nao_como_erro(tela, indice):
    s, base = tela
    s.registra_erro("limiar", "publicação automática desligada: sem data/avaliacao/limiar.json", uma_vez=True)
    s.registra_erro("publicar", "falha ao publicar: git push: rede")

    estado = json.loads(pede(base + "/estado")[1])

    assert estado["automatica"] == {"limiar": None, "motivo": "sem data/avaliacao/limiar.json"}
    assert [e["etapa"] for e in estado["erros"]] == ["publicar"]
    assert [q["nome"] for q in estado["painel_erros"]] == [
        "Transcrição e áudio", "API do Claude", "API do Google (base)", "Publicação"]
    assert estado["base"] == {"checagens": len(indice.registros)}
