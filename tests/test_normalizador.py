"""Normalizador (ponto de teste 2): resposta bruta de cada fonte → registros de checagem.

Os exemplos fixos são recortes de respostas reais (só metadados) em tests/fixtures/.
"""
import json
from pathlib import Path

from checagens import junta, normaliza

FIXTURES = Path(__file__).parent / "fixtures"
COLETA = "2026-10-20T15:00:00Z"


def google(nome):
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


def por_url(registros):
    return {r["url"]: r for r in registros}


def test_google_api_extrai_os_campos_da_checagem():
    registros = normaliza("google_api", google("gfc_flavio_p0.json"), COLETA, busca="Flávio Bolsonaro")
    r = por_url(registros)["https://checamos.afp.com/doc.afp.com.D3Q24CQ"]
    assert r == {
        "id": "https://checamos.afp.com/doc.afp.com.D3Q24CQ",
        "url": "https://checamos.afp.com/doc.afp.com.D3Q24CQ",
        "agencia": "AFP Checamos",
        "agencia_na_fonte": "AFP Checamos",
        "site": "checamos.afp.com",
        "frase": "Imagem mostra Flávio Bolsonaro na capa da revista IstoÉ como o \"anti-Lula\"",
        "quem_disse": "Várias fontes",
        "idioma": "pt",
        "data": "2026-10-19",
        "data_origem": "revisao",
        "rotulo": "Falso",
        "coletado_em": COLETA,
        "fonte_coleta": "google_api",
        "buscas": ["Flávio Bolsonaro"],
    }


def test_google_api_sem_data_de_revisao_usa_a_data_da_url_como_publicacao():
    registros = normaliza("google_api", google("gfc_flavio_p0.json"), COLETA)
    r = por_url(registros)["https://noticias.uol.com.br/confere/ultimas-noticias/2026/10/20/"
                           "istoe-nao-publicou-capa-com-flavio-bolsonaro-imagem-foi-alterada-por-ia.ghtm"]
    assert (r["data"], r["data_origem"]) == ("2026-10-20", "publicacao")


def test_google_api_sem_data_de_revisao_nem_na_url_usa_a_data_da_afirmacao():
    registros = normaliza("google_api", google("gfc_flavio_p0.json"), COLETA)
    r = [r for r in registros if r["frase"].startswith("Flávio diz que irá cortar todos os benefícios")][0]
    assert r["agencia"] == "Estadão"
    assert (r["data"], r["data_origem"]) == ("2026-10-15", "afirmacao")


def test_o_id_e_a_url_sem_o_parametro_de_modelo_que_o_bol_deixa_na_url():
    registros = normaliza("google_api", google("gfc_url_com_modelo.json"), COLETA)
    bol = [r["id"] for r in registros if r["agencia"] == "BOL - UOL"]
    assert bol == [
        "https://www.bol.uol.com.br/noticias/2026/10/16/desinformacao-ia-falso-posts-imagens-x-atos-pro-lula-paulista-copacabana.htm",
        "https://www.bol.uol.com.br/noticias/2026/07/01/prefeito-de-cuiaba-nao-foi-vaiado-com-coro-de-lula-lula.ghtm",
    ]


def test_a_mesma_url_vinda_de_duas_buscas_vira_um_registro_so():
    # a checagem do Comprova sobre a Meta AI aparece nas duas respostas
    a = normaliza("google_api", google("gfc_flavio_p0.json"), COLETA, busca="Flávio Bolsonaro")
    b = normaliza("google_api", google("gfc_flavio_p1.json"), "2026-10-20T16:00:00Z", busca="Meta AI Flávio")
    base = junta(a + b)
    assert len(base) == 22
    comprova = [r for r in base if r["agencia"] == "Projeto Comprova"]
    assert len(comprova) == 1
    assert comprova[0]["buscas"] == ["Flávio Bolsonaro", "Meta AI Flávio"]
    assert comprova[0]["coletado_em"] == COLETA


def explorer(nome):
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_explorer_extrai_os_campos_da_checagem():
    registros = normaliza("google_explorer", explorer("gfce_flavio_p0.txt"), COLETA, busca="Flávio Bolsonaro")
    assert len(registros) == 4
    r = por_url(registros)["https://checamos.afp.com/doc.afp.com.D3HE2WE"]
    assert r == {
        "id": "https://checamos.afp.com/doc.afp.com.D3HE2WE",
        "url": "https://checamos.afp.com/doc.afp.com.D3HE2WE",
        "agencia": "AFP Checamos",
        "agencia_na_fonte": "AFP Checamos",
        "site": "checamos.afp.com",
        "frase": "Flávio Bolsonaro diz que vai tirar a pensão de aposentados",
        "quem_disse": "Várias fontes",
        "idioma": "pt",
        "data": "2026-10-16",
        "data_origem": "revisao",
        "rotulo": "Falso",
        "coletado_em": COLETA,
        "fonte_coleta": "google_explorer",
        "buscas": ["Flávio Bolsonaro"],
    }


def test_explorer_sem_data_de_revisao_usa_a_data_de_publicacao():
    registros = por_url(normaliza("google_explorer", explorer("gfce_flavio_p0.txt"), COLETA))
    uol = registros["https://noticias.uol.com.br/confere/ultimas-noticias/2026/10/16/"
                    "e-falso-que-papa-leao-14-declarou-apoio-a-flavio-bolsonaro.ghtm"]
    assert (uol["agencia"], uol["data"], uol["data_origem"]) == ("UOL Notícias", "2026-10-16", "publicacao")
    estadao = registros["https://www.estadao.com.br/estadao-verifica/"
                        "publicacao-manipulou-entrevista-flavio-bolsonaro-cortar-programas-sociais/"]
    assert (estadao["quem_disse"], estadao["data"], estadao["data_origem"]) == ("Redes Sociais", "2026-10-15", "publicacao")
