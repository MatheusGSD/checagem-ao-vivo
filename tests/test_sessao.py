"""Sessão de checagem (ponto de teste 1): entram falas, saem a fila de ligações propostas e o
arquivo de dados da página.

O Claude é trocado por um dublê com respostas prontas no formato da saída estruturada. A busca
por sentido roda de verdade (multilingual-e5-small) sobre a base de exemplo de 20 checagens
fictícias em tests/fixtures/base_sessao.json.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from base_checagens import Indice
from contrato_painel import valida
from sessao import Sessao

FIXTURES = Path(__file__).parent / "fixtures"
BRASILIA = timezone(timedelta(hours=-3))

DESEMPREGO_2025 = "https://example.org/aosfatos/desemprego-2025"
DESEMPREGO_2023 = "https://example.org/estadao/desemprego-2023"
LICENCA_2022 = "https://example.org/aosfatos/licenca-maternidade"
PIX_ESTADAO = "https://example.org/estadao/pix-bolsonaro"
PIX_AFP = "https://example.org/afp/pix-criacao"
GASOLINA_2022 = "https://example.org/uol/gasolina-2022"

FALA_DESEMPREGO = ("Olha, o meu governo gerou emprego como nunca. Hoje o desemprego é o menor da história "
                   "do IBGE, e isso ninguém pode negar.")
FALA_LICENCA = ("Em 2009 você foi o único deputado da Assembleia que votou contra a licença-maternidade "
                "de seis meses para as servidoras.")
FALA_PIX = "Quem criou o Pix foi o governo do meu pai, isso todo mundo sabe."
FALA_GASOLINA = "No nosso tempo a gasolina caiu trinta por cento no posto em um ano."
FALA_MEDIACAO = "Candidato Lula, o senhor tem um minuto para a resposta sobre o desemprego."


@pytest.fixture(scope="module")
def indice():
    registros = json.loads((FIXTURES / "base_sessao.json").read_text(encoding="utf-8"))["registros"]
    return Indice(registros)


def afirmacao(trecho, reformulacao, tipo, confirma=None):
    """Uma afirmação que o dublê extrai, e as checagens que ele confirmaria para ela:
    confirma = {URL: (nota, motivo)}, valendo só para as que vierem entre as candidatas."""
    return {"trecho": trecho, "reformulacao": reformulacao, "tipo": tipo, "confirma": confirma or {}}


DESEMPREGO = afirmacao("o desemprego é o menor da história do IBGE",
                       "A taxa de desemprego é a menor da série histórica do IBGE.", "numero",
                       {DESEMPREGO_2025: (0.92, "Mesma afirmação: desemprego no menor nível da série do IBGE.")})


class Claude:
    """Dublê do Claude: devolve respostas prontas no formato da saída estruturada.
    falas = {texto da fala: [afirmação(...), ...]}."""

    def __init__(self, falas):
        self.falas = falas
        self.chamadas = []

    def __call__(self, etapa, prompt, conteudo, schema):
        self.chamadas.append(prompt)
        if prompt == "extrair_afirmacoes.md":
            afirmacoes = next((v for fala, v in self.falas.items() if fala in conteudo), [])
            saida = {"afirmacoes": [{k: a[k] for k in ("trecho", "reformulacao", "tipo")} for a in afirmacoes]}
        else:
            [a] = [a for v in self.falas.values() for a in v if a["trecho"] in conteudo]
            saida = {"confirmadas": [{"url": url, "nota": nota, "motivo": motivo}
                                     for url, (nota, motivo) in a["confirma"].items() if url in conteudo]}
        return saida, {"etapa": etapa, "modelo_respondeu": "dublê", "prompt_sha256": "0" * 64,
                       "tokens": {"entrada": 0, "saida": 0}}

    def extracoes(self):
        return self.chamadas.count("extrair_afirmacoes.md")

    def confirmacoes(self):
        return self.chamadas.count("confirmar_ligacao.md")


def relogio():
    return datetime(2026, 10, 23, 21, 40, tzinfo=BRASILIA)


def sessao(indice, claude):
    return Sessao(indice, chamar=claude, relogio=relogio)


def test_fala_com_afirmacao_que_bate_com_a_base_gera_ligacao_proposta_na_fila(indice):
    s = sessao(indice, Claude({FALA_DESEMPREGO: [DESEMPREGO]}))

    s.recebe_fala("Lula", 754.3, FALA_DESEMPREGO)

    [lig] = s.fila()
    assert (lig["quem"], lig["minutagem_s"]) == ("Lula", 754.3)
    assert lig["trecho"] == "o desemprego é o menor da história do IBGE"
    assert lig["reformulacao"] == "A taxa de desemprego é a menor da série histórica do IBGE."
    assert lig["tipo"] == "numero"
    assert lig["fala"] == FALA_DESEMPREGO
    assert lig["nota"] == 0.92
    [chk] = lig["checagens"]
    assert chk["url"] == DESEMPREGO_2025
    assert (chk["agencia"], chk["data"], chk["rotulo"]) == ("Aos Fatos", "2025-08-14", "Verdadeiro")
    assert (chk["nota"], chk["motivo"]) == (0.92, "Mesma afirmação: desemprego no menor nível da série do IBGE.")


def test_trecho_que_nao_e_literal_e_descartado_e_registrado(indice):
    # a transcrição diz "trinta por cento"; o trecho devolvido corrige para "30%"
    corrigido = afirmacao("a gasolina caiu 30% no posto em um ano",
                          "O preço da gasolina caiu 30% em um ano no governo Bolsonaro.", "numero",
                          {GASOLINA_2022: (0.9, "Mesmo número.")})
    claude = Claude({FALA_GASOLINA: [corrigido]})
    s = sessao(indice, claude)

    s.recebe_fala("Flávio", 120.0, FALA_GASOLINA)

    assert s.fila() == []
    assert s.exporta()["sem_par"] == []
    [d] = s.descartados()
    assert (d["quem"], d["minutagem_s"], d["trecho"]) == ("Flávio", 120.0, "a gasolina caiu 30% no posto em um ano")
    assert claude.confirmacoes() == 0


def test_afirmacao_numerica_nao_liga_com_checagem_anterior_a_2025(indice):
    # o dublê confirmaria as checagens antigas se elas chegassem até ele
    desemprego = afirmacao("o desemprego é o menor da história do IBGE",
                           "A taxa de desemprego é a menor da série histórica do IBGE.", "numero",
                           {DESEMPREGO_2025: (0.9, "Mesmo indicador."), DESEMPREGO_2023: (0.9, "Mesmo indicador.")})
    gasolina = afirmacao("a gasolina caiu trinta por cento no posto em um ano",
                         "O preço da gasolina caiu 30% em um ano no governo Bolsonaro.", "numero",
                         {GASOLINA_2022: (0.9, "Mesmo número.")})
    s = sessao(indice, Claude({FALA_DESEMPREGO: [desemprego], FALA_GASOLINA: [gasolina]}))

    s.recebe_fala("Lula", 754.3, FALA_DESEMPREGO)
    s.recebe_fala("Flávio", 790.0, FALA_GASOLINA)

    [lig] = s.fila()
    assert [c["url"] for c in lig["checagens"]] == [DESEMPREGO_2025]
    assert s.exporta()["sem_par"] == [{"minutagem_s": 790.0, "quem": "Flávio",
                                       "trecho": "a gasolina caiu trinta por cento no posto em um ano",
                                       "reformulacao": "O preço da gasolina caiu 30% em um ano no governo Bolsonaro."}]


def test_fato_perene_liga_com_checagem_antiga(indice):
    licenca = afirmacao(
        "você foi o único deputado da Assembleia que votou contra a licença-maternidade de seis meses",
        "Flávio Bolsonaro foi o único deputado da Alerj a votar contra a licença-maternidade de seis meses "
        "em 2009.", "perene", {LICENCA_2022: (0.88, "Mesmo voto na Alerj em 2009.")})
    s = sessao(indice, Claude({FALA_LICENCA: [licenca]}))

    s.recebe_fala("Lula", 30.0, FALA_LICENCA)

    [lig] = s.fila()
    [chk] = lig["checagens"]
    assert (chk["url"], chk["data"]) == (LICENCA_2022, "2022-03-18")


def test_duas_agencias_na_mesma_afirmacao_aparecem_as_duas_com_os_rotulos_originais(indice):
    pix = afirmacao("Quem criou o Pix foi o governo do meu pai", "O Pix foi criado no governo de Jair Bolsonaro.",
                    "perene", {PIX_ESTADAO: (0.95, "Mesma afirmação sobre a criação do Pix."),
                               PIX_AFP: (0.9, "Mesma afirmação, atribuída a redes sociais.")})
    s = sessao(indice, Claude({FALA_PIX: [pix]}))

    s.recebe_fala("Flávio", 400.0, FALA_PIX)

    [lig] = s.fila()
    assert {(c["agencia"], c["rotulo"]) for c in lig["checagens"]} == {("Estadão", "Enganoso"),
                                                                      ("AFP Checamos", "Não é bem assim")}


def test_falas_da_mediacao_nao_geram_chamada_de_extracao(indice):
    # mesmo que o Claude encontrasse uma afirmação na fala da mediação
    claude = Claude({FALA_MEDIACAO: [afirmacao("o senhor tem um minuto", "Lula tem um minuto.", "numero")],
                     FALA_DESEMPREGO: [DESEMPREGO]})
    s = sessao(indice, claude)

    s.recebe_fala("Mediação", 750.0, FALA_MEDIACAO)
    assert claude.chamadas == []

    s.recebe_fala("Lula", 754.3, FALA_DESEMPREGO)
    assert claude.extracoes() == 1
    assert [lig["quem"] for lig in s.fila()] == ["Lula"]


def test_exportacao_segue_o_contrato_da_pagina(indice):
    gasolina = afirmacao("a gasolina caiu trinta por cento no posto em um ano",
                         "O preço da gasolina caiu 30% em um ano no governo Bolsonaro.", "numero")
    s = sessao(indice, Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_GASOLINA: [gasolina]}))
    s.recebe_fala("Lula", 754.3, FALA_DESEMPREGO)
    s.recebe_fala("Flávio", 790.0, FALA_GASOLINA)

    dados = s.exporta()

    assert valida(dados) == []
    assert dados["meta"]["atualizado_em"] == "2026-10-23T21:40:00-03:00"
    assert dados["meta"]["modelo"] == "claude-opus-5-5"
    assert {p["arquivo"] for p in dados["meta"]["prompts"]} == {"prompts/extrair_afirmacoes.md",
                                                               "prompts/confirmar_ligacao.md"}
    assert dados["meta"]["limiar_automatico"] is None
    # nesta etapa nada é publicado: a ligação proposta espera na fila
    assert (dados["ligacoes"], dados["correcoes"]) == ([], [])
    assert [a["quem"] for a in dados["sem_par"]] == ["Flávio"]


def test_repassar_varias_falas_de_uma_vez_da_o_mesmo_resultado_que_uma_a_uma(indice):
    gasolina = afirmacao("a gasolina caiu trinta por cento no posto em um ano",
                         "O preço da gasolina caiu 30% em um ano no governo Bolsonaro.", "numero")
    pix = afirmacao("Quem criou o Pix foi o governo do meu pai", "O Pix foi criado no governo de Jair Bolsonaro.",
                    "perene", {PIX_ESTADAO: (0.95, "Mesma afirmação.")})
    falas = [("Mediação", 750.0, FALA_MEDIACAO), ("Lula", 754.3, FALA_DESEMPREGO),
             ("Flávio", 790.0, FALA_GASOLINA), ("Flávio", 800.0, FALA_PIX)]
    um_a_um = sessao(indice, Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_GASOLINA: [gasolina], FALA_PIX: [pix]}))
    for f in falas:
        um_a_um.recebe_fala(*f)
    de_uma_vez = sessao(indice, Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_GASOLINA: [gasolina], FALA_PIX: [pix]}))

    de_uma_vez.recebe_falas(falas, paralelo=3)

    assert de_uma_vez.fila() == um_a_um.fila()
    assert de_uma_vez.exporta() == um_a_um.exporta()
    assert [lig["trecho"] for lig in de_uma_vez.fila()] == [DESEMPREGO["trecho"], pix["trecho"]]


FOF_13 = ("https://g1.globo.com/fato-ou-fake/noticia/2026/10/23/"
          "e-fake-video-de-flavio-bolsonaro-dizendo-que-vai-cortar-13-e-aposentadorias.ghtml")
CHECAGEM_DA_NOITE = {
    "id": FOF_13, "url": FOF_13, "agencia": "Fato ou Fake (g1)", "site": "g1.globo.com",
    "frase": "Vídeo de Flávio Bolsonaro dizendo que, se eleito, vai cortar 13° e aposentadorias; fala foi manipulada",
    "quem_disse": None, "idioma": "pt", "data": "2026-10-23", "data_origem": "publicacao", "rotulo": "#FAKE",
    "coletado_em": "2026-10-24T00:50:00Z", "fonte_coleta": "fato_ou_fake_rss", "buscas": [],
}
FALA_13 = "E o meu adversário já disse que, se ganhar, vai cortar o décimo terceiro e as aposentadorias."


def test_atualizacao_da_base_liga_uma_afirmacao_que_estava_sem_par():
    registros = json.loads((FIXTURES / "base_sessao.json").read_text(encoding="utf-8"))["registros"]
    corte = afirmacao("vai cortar o décimo terceiro e as aposentadorias",
                      "Flávio Bolsonaro disse que, se eleito, vai cortar o 13º salário e as aposentadorias.",
                      "perene", {FOF_13: (0.9, "Mesma fala atribuída a Flávio sobre cortar 13º e aposentadorias.")})
    gasolina = afirmacao("a gasolina caiu trinta por cento no posto em um ano",
                         "O preço da gasolina caiu 30% em um ano no governo Bolsonaro.", "numero")
    s = sessao(Indice(registros), Claude({FALA_13: [corte], FALA_GASOLINA: [gasolina]}))
    s.recebe_fala("Lula", 1500.0, FALA_13)
    s.recebe_fala("Flávio", 1530.0, FALA_GASOLINA)
    assert s.fila() == []
    assert [a["trecho"] for a in s.exporta()["sem_par"]] == [corte["trecho"], gasolina["trecho"]]

    s.atualiza_base([CHECAGEM_DA_NOITE])

    [lig] = s.fila()
    assert (lig["quem"], lig["minutagem_s"], lig["trecho"]) == ("Lula", 1500.0, corte["trecho"])
    [chk] = lig["checagens"]
    assert (chk["url"], chk["agencia"], chk["rotulo"], chk["data"]) == (FOF_13, "Fato ou Fake (g1)", "#FAKE", "2026-10-23")
    assert [a["trecho"] for a in s.exporta()["sem_par"]] == [gasolina["trecho"]]


def test_falha_do_claude_numa_fala_fica_registrada_e_a_sessao_segue(indice):
    dublê = Claude({FALA_DESEMPREGO: [DESEMPREGO]})

    def chamar(etapa, prompt, conteudo, schema):
        if FALA_PIX in conteudo:
            raise SystemExit("extrair: o modelo recusou")  # como agente.chama faz numa recusa
        return dublê(etapa, prompt, conteudo, schema)

    s = Sessao(indice, chamar=chamar, relogio=relogio)
    s.recebe_fala("Flávio", 400.0, FALA_PIX)
    s.recebe_fala("Lula", 754.3, FALA_DESEMPREGO)

    [erro] = s.erros()
    assert (erro["quem"], erro["minutagem_s"]) == ("Flávio", 400.0)
    assert "recusou" in erro["mensagem"]
    assert len(s.fila()) == 1
