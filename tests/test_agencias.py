"""Política de agências (data/agencias.json): quem fica fora da base e com que nome cada agência aparece.

Os pontos testados são `agencias.separa(registros, politica)` (o que entra na base e o que fica de
fora) e `agencias.agencias_do_painel(registros, politica)` (as listas dos metadados da página).
"""
import json
from pathlib import Path

from agencias import agencias_do_painel, carrega, separa
from checagens import normaliza

FIXTURES = Path(__file__).parent / "fixtures"

POLITICA_TESTE = {
    "excluidas": [
        {"nome": "Observador", "motivo": "Checador português.", "nomes_na_fonte": ["Observador"],
         "sites": ["observador.pt"]},
        {"nome": "Lupa", "motivo": "Não autorizou a coleta.", "nomes_na_fonte": ["Lupa"], "sites": ["lupa.news"]},
    ],
    "nomes_exibidos": {"BOL - UOL": "BOL", "Agência Publica": "Agência Pública"},
    "vias": {"google_api": "ClaimReview na API do Google"},
}


def registro(agencia, site, url, rotulo="Falso", fonte="google_api"):
    return {"id": url, "url": url, "agencia": agencia, "agencia_na_fonte": agencia, "site": site,
            "frase": "frase", "rotulo": rotulo, "fonte_coleta": fonte, "buscas": []}


def test_agencia_excluida_sai_da_base_pelo_nome_ou_pelo_site():
    regs = [registro("Observador", "observador.pt", "https://observador.pt/1"),
            registro("Observador Fact Check", "observador.pt", "https://observador.pt/2"),
            registro("Agência Lupa", "lupa.news", "https://lupa.news/3"),
            registro("Aos Fatos", "aosfatos.org", "https://aosfatos.org/4")]
    dentro, fora = separa(regs, POLITICA_TESTE)
    assert [r["url"] for r in dentro] == ["https://aosfatos.org/4"]
    assert [r["url"] for r in fora] == ["https://observador.pt/1", "https://observador.pt/2", "https://lupa.news/3"]


def test_grafias_diferentes_aparecem_com_um_nome_so_e_o_nome_da_fonte_fica_guardado():
    regs = [registro("BOL - UOL", "bol.uol.com.br", "https://bol.uol.com.br/1", rotulo="Falso"),
            registro("BOL", "bol.uol.com.br", "https://bol.uol.com.br/2", rotulo="Enganoso"),
            registro("Agência Publica", "apublica.org", "https://apublica.org/3", rotulo="Exagerado")]
    dentro, _ = separa(regs, POLITICA_TESTE)
    assert [(r["agencia"], r["agencia_na_fonte"], r["rotulo"]) for r in dentro] == [
        ("BOL", "BOL - UOL", "Falso"),
        ("BOL", "BOL", "Enganoso"),
        ("Agência Pública", "Agência Publica", "Exagerado"),
    ]


def test_separa_nao_mexe_nos_registros_recebidos():
    regs = [registro("BOL - UOL", "bol.uol.com.br", "https://bol.uol.com.br/1")]
    separa(regs, POLITICA_TESTE)
    assert regs[0]["agencia"] == "BOL - UOL"


def test_metadados_listam_as_agencias_da_base_pelo_nome_exibido_e_todas_as_excluidas_com_motivo():
    regs = [registro("BOL - UOL", "bol.uol.com.br", "https://bol.uol.com.br/1"),
            registro("BOL", "bol.uol.com.br", "https://bol.uol.com.br/2"),
            registro("Aos Fatos", "aosfatos.org", "https://aosfatos.org/3"),
            registro("Observador", "observador.pt", "https://observador.pt/4")]
    assert agencias_do_painel(regs, POLITICA_TESTE) == {
        "agencias_incluidas": [{"nome": "Aos Fatos", "via": "ClaimReview na API do Google"},
                               {"nome": "BOL", "via": "ClaimReview na API do Google"}],
        "agencias_excluidas": [{"nome": "Observador", "motivo": "Checador português."},
                               {"nome": "Lupa", "motivo": "Não autorizou a coleta."}],
    }


def test_via_junta_os_canais_pelos_quais_a_agencia_chegou():
    regs = [registro("Aos Fatos", "aosfatos.org", "https://aosfatos.org/1"),
            registro("Aos Fatos", "aosfatos.org", "https://aosfatos.org/2", fonte="fato_ou_fake")]
    assert agencias_do_painel(regs, POLITICA_TESTE)["agencias_incluidas"] == [
        {"nome": "Aos Fatos", "via": "ClaimReview na API do Google e fato_ou_fake"}]


# ---------- a política versionada do repo ----------

def test_politica_do_repo_exclui_observador_e_lupa_com_motivo():
    politica = carrega()
    motivos = {e["nome"]: e["motivo"] for e in politica["excluidas"]}
    assert "brasileiros" in motivos["Observador"]
    assert "ClaimReview" in motivos["Lupa"] and "22/10" in motivos["Lupa"]


def test_politica_do_repo_unifica_as_grafias_vistas_na_coleta():
    regs = normaliza("google_api", json.loads((FIXTURES / "gfc_url_com_modelo.json").read_text(encoding="utf-8")),
                     "2026-10-20T15:00:00Z")
    regs += [registro(a, s, f"https://{s}/{i}") for i, (a, s) in enumerate([
        ("Agência Pública", "apublica.org"), ("Agência Publica", "apublica.org"),
        ("Estadão", "estadao.com.br"), ("Acervo - Estadão", "acervo.estadao.com.br"),
        ("BOL", "bol.uol.com.br"), ("Observador", "observador.pt")])]
    dentro, fora = separa(regs, carrega())
    assert {r["agencia_na_fonte"] for r in fora} == {"Observador"}
    nomes = {r["agencia_na_fonte"]: r["agencia"] for r in dentro}
    assert nomes["Agência Publica"] == nomes["Agência Pública"]
    assert nomes["Acervo - Estadão"] == nomes["Estadão"]
    assert nomes["BOL - UOL"] == nomes["BOL"]

