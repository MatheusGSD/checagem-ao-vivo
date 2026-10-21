"""Tela de aprovação: as ações chegam à sessão por HTTP, e só a partir da própria máquina e da própria tela."""
import json
import urllib.error
import urllib.request
from datetime import timedelta

import pytest

from aprovacao import serve
from base_checagens import Indice
from sessao import Sessao
from test_sessao import FALA_PIX, FIXTURES, INICIO, PIX, PIX_ESTADAO, Claude, Relogio


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
