"""Sessão de checagem (ponto de teste 1): entram falas, saem a fila de ligações propostas e o
arquivo de dados da página.

O Claude é trocado por um dublê com respostas prontas no formato da saída estruturada. A busca
por sentido roda de verdade (multilingual-e5-small) sobre a base de exemplo de 20 checagens
fictícias em tests/fixtures/base_sessao.json.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agente import RespostaInvalida
from base_checagens import Indice
from contrato_painel import valida
import sessao as sessao_mod
from sessao import Sessao

FIXTURES = Path(__file__).parent / "fixtures"
from comum import BRASILIA  # noqa: E402

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


def pedido(schema):
    """Que resposta o pedido espera, pela forma da saída estruturada (e não pelo prompt): "extracao"
    (lista de afirmações) ou "confirmacao" (lista de checagens confirmadas)."""
    return "extracao" if "afirmacoes" in schema["properties"] else "confirmacao"


def resposta(etapa, saida, modelo="dublê"):
    return saida, {"etapa": etapa, "modelo_respondeu": modelo, "prompt_sha256": "0" * 64,
                   "tokens": {"entrada": 0, "saida": 0}}


class Claude:
    """Dublê do Claude: devolve respostas prontas no formato da saída estruturada.
    falas = {texto da fala: [afirmação(...), ...]}. Reconhece a fala e a afirmação pelo texto que
    chega no pedido e confirma só as checagens que chegaram entre as candidatas."""

    def __init__(self, falas, modelo="dublê"):
        self.falas = falas
        self.modelo = modelo
        self.chamadas = []

    def __call__(self, etapa, prompt, conteudo, schema):
        self.chamadas.append(pedido(schema))
        if pedido(schema) == "extracao":
            afirmacoes = next((v for fala, v in self.falas.items() if fala in conteudo), [])
            saida = {"afirmacoes": [{k: a[k] for k in ("trecho", "reformulacao", "tipo")} for a in afirmacoes]}
        else:
            [a] = [a for v in self.falas.values() for a in v if a["trecho"] in conteudo]
            saida = {"confirmadas": [{"url": url, "nota": nota, "motivo": motivo}
                                     for url, (nota, motivo) in a["confirma"].items() if url in conteudo]}
        return resposta(etapa, saida, self.modelo)

    def extracoes(self):
        return self.chamadas.count("extracao")

    def confirmacoes(self):
        return self.chamadas.count("confirmacao")


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


def test_fala_que_comecou_antes_do_00_00_aparece_no_painel_com_minutagem_zero(indice):
    # a linha de falas entrega o começo de verdade (negativo) da fala que estava em andamento no 00:00
    s = sessao(indice, Claude({FALA_PIX: [PIX]}))

    s.recebe_fala("Flávio", -34.2, FALA_PIX)

    [lig] = s.fila()
    assert lig["minutagem_s"] == 0.0
    s.aprova(lig["id"])
    assert s.exporta()["ligacoes"][0]["minutagem_s"] == 0.0
    assert valida(s.exporta()) == []


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


def test_afirmacao_sem_reformulacao_e_descartada_e_o_painel_segue_dentro_do_contrato(indice):
    # o esquema da extração aceita texto vazio; uma reformulação vazia no painel deixaria a sessão sem
    # conseguir exportar (nem reabrir), porque o contrato exige texto não vazio
    vazia = afirmacao("o desemprego é o menor da história do IBGE", "  ", "numero")
    claude = Claude({FALA_DESEMPREGO: [vazia]})
    s = sessao(indice, claude)

    s.recebe_fala("Lula", 300.0, FALA_DESEMPREGO)

    assert s.exporta()["sem_par"] == [] and s.fila() == []
    [d] = s.descartados()
    assert d["trecho"] == "o desemprego é o menor da história do IBGE"
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


def test_metodo_mostra_os_modelos_que_de_fato_responderam(indice):
    # a API pode refazer a chamada num modelo de reserva: a confirmação aqui saiu de outro modelo
    dublê = Claude({FALA_PIX: [PIX]})

    def chamar(etapa, prompt, conteudo, schema):
        saida, reg = dublê(etapa, prompt, conteudo, schema)
        return saida, {**reg, "modelo_respondeu": "claude-sonnet-5-5" if pedido(schema) == "extracao"
                       else "claude-opus-5-5"}

    s = Sessao(indice, chamar=chamar, relogio=relogio)
    assert s.exporta()["meta"]["modelos_que_responderam"] == []

    s.recebe_fala("Flávio", 400.0, FALA_PIX)

    dados = s.exporta()
    assert dados["meta"]["modelo"] == "claude-sonnet-5-5"  # o pedido
    assert dados["meta"]["modelos_que_responderam"] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert valida(dados) == []
    copia = Sessao.reconstroi(indice, json.loads(json.dumps(s.eventos)), chamar=Claude({}), relogio=relogio)
    assert copia.exporta()["meta"]["modelos_que_responderam"] == ["claude-opus-5-5", "claude-sonnet-5-5"]


def test_exportacao_segue_o_contrato_da_pagina(indice):
    gasolina = afirmacao("a gasolina caiu trinta por cento no posto em um ano",
                         "O preço da gasolina caiu 30% em um ano no governo Bolsonaro.", "numero")
    s = sessao(indice, Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_GASOLINA: [gasolina]}))
    s.recebe_fala("Lula", 754.3, FALA_DESEMPREGO)
    s.recebe_fala("Flávio", 790.0, FALA_GASOLINA)

    dados = s.exporta()

    assert valida(dados) == []
    assert dados["meta"]["atualizado_em"] == "2026-10-23T21:40:00-03:00"
    assert dados["meta"]["modelo"] == "claude-sonnet-5-5"
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
    assert lig["ligada_depois"] is True
    [chk] = lig["checagens"]
    assert (chk["url"], chk["agencia"], chk["rotulo"], chk["data"]) == (FOF_13, "Fato ou Fake (g1)", "#FAKE", "2026-10-23")
    assert [a["trecho"] for a in s.exporta()["sem_par"]] == [gasolina["trecho"]]


def test_falha_do_claude_numa_fala_fica_registrada_e_a_sessao_segue(indice):
    dublê = Claude({FALA_DESEMPREGO: [DESEMPREGO]})

    def chamar(etapa, prompt, conteudo, schema):
        if FALA_PIX in conteudo:
            raise RespostaInvalida("extrair: o modelo recusou")  # como agente.chama faz numa recusa
        return dublê(etapa, prompt, conteudo, schema)

    s = Sessao(indice, chamar=chamar, relogio=relogio)
    s.recebe_fala("Flávio", 400.0, FALA_PIX)
    s.recebe_fala("Lula", 754.3, FALA_DESEMPREGO)

    assert {(e["quem"], e["minutagem_s"]) for e in s.erros()} == {("Flávio", 400.0)}
    assert "recusou" in s.erros()[0]["mensagem"]
    assert len(s.fila()) == 1


def test_pedido_de_encerrar_o_processo_durante_a_chamada_nao_vira_erro_da_fala(indice):
    def chamar(etapa, prompt, conteudo, schema):
        raise SystemExit("encerrando")  # Ctrl+Break, sys.exit de outra parte: não é falha do Claude

    s = Sessao(indice, chamar=chamar, relogio=relogio)

    with pytest.raises(SystemExit):
        s.recebe_fala("Flávio", 400.0, FALA_PIX)
    assert s.erros() == []


# ---------- operador: aprovação, publicação automática, correções e retomada ----------

INICIO = datetime(2026, 10, 23, 21, 20, tzinfo=BRASILIA)

PIX = afirmacao("Quem criou o Pix foi o governo do meu pai", "O Pix foi criado no governo de Jair Bolsonaro.",
                "perene", {PIX_ESTADAO: (0.95, "Mesma afirmação sobre a criação do Pix."),
                           PIX_AFP: (0.9, "Mesma afirmação, atribuída a redes sociais.")})


class Relogio:
    """Relógio que o teste adianta."""

    def __init__(self, agora):
        self.agora = agora

    def __call__(self):
        return self.agora

    def adianta(self, **kw):
        self.agora += timedelta(**kw)


def test_aprovar_com_selo_faz_o_painel_exibir_o_selo_e_nao_o_rotulo_do_claimreview(indice):
    relogio = Relogio(INICIO + timedelta(minutes=30))
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=relogio)
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)
    [proposta] = s.fila()
    relogio.adianta(seconds=40)

    # o ClaimReview do Estadão diz "Enganoso"; na página o operador vê "Falso". A AFP fica sem selo informado.
    s.aprova(proposta["id"], selos={PIX_ESTADAO: "Falso"})

    assert s.fila() == []
    [lig] = s.exporta()["ligacoes"]
    assert (lig["id"], lig["origem"], lig["publicada_em"]) == (proposta["id"], "aprovada", "2026-10-23T21:50:40-03:00")
    assert (lig["quem"], lig["minutagem_s"], lig["trecho"]) == ("Flávio", 1800.0, PIX["trecho"])
    assert lig["ligada_depois"] is False
    assert {c["url"]: (c["agencia"], c["rotulo"], c["fonte_rotulo"], c["origem"]) for c in lig["checagens"]} == {
        PIX_ESTADAO: ("Estadão", "Falso", "selo_da_pagina", "aprovada"),
        PIX_AFP: ("AFP Checamos", "Não é bem assim", "dados_da_agencia", "aprovada"),
    }


def test_editar_reformulacao_e_trecho_da_ligacao_na_fila_vale_no_painel_ao_aprovar(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=relogio)
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)
    [proposta] = s.fila()

    s.edita(proposta["id"], trecho="Quem criou o Pix foi o governo do meu pai, isso todo mundo sabe",
            reformulacao="Flávio Bolsonaro disse que o Pix foi criado no governo de Jair Bolsonaro.")

    [editada] = s.fila()
    assert editada["trecho"] == "Quem criou o Pix foi o governo do meu pai, isso todo mundo sabe"
    s.aprova(editada["id"])
    [lig] = s.exporta()["ligacoes"]
    assert (lig["trecho"], lig["reformulacao"]) == (
        "Quem criou o Pix foi o governo do meu pai, isso todo mundo sabe",
        "Flávio Bolsonaro disse que o Pix foi criado no governo de Jair Bolsonaro.")
    copia = Sessao.reconstroi(indice, json.loads(json.dumps(s.eventos)), chamar=Claude({}), relogio=relogio)
    assert estado(copia) == estado(s)


def test_editar_com_trecho_que_nao_esta_na_fala_letra_a_letra_nao_vale(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=relogio)
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)
    ident = s.fila()[0]["id"]
    n = len(s.eventos)

    with pytest.raises(ValueError):
        s.edita(ident, trecho="Quem inventou o Pix foi o governo do meu pai")
    with pytest.raises(ValueError):
        s.edita(ident, reformulacao="  ")

    assert len(s.eventos) == n
    assert s.fila()[0]["trecho"] == PIX["trecho"]


def test_editar_so_mexe_no_que_foi_informado(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=relogio)
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)
    ident = s.fila()[0]["id"]

    s.edita(ident, reformulacao="O Pix foi criado no governo Bolsonaro, segundo Flávio.")

    [p] = s.fila()
    assert (p["trecho"], p["reformulacao"]) == (PIX["trecho"], "O Pix foi criado no governo Bolsonaro, segundo Flávio.")


def test_aprovar_so_parte_das_checagens_publica_so_essas(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=Relogio(INICIO + timedelta(minutes=30)))
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)

    s.aprova(s.fila()[0]["id"], manter=[PIX_AFP])

    [lig] = s.exporta()["ligacoes"]
    assert [c["url"] for c in lig["checagens"]] == [PIX_AFP]


def sessao_com_limiar(indice, claude, relogio, limiar):
    s = Sessao(indice, chamar=claude, relogio=relogio, meta={"limiar_automatico": limiar})
    s.marca_inicio(INICIO)
    return s


def test_ligacao_acima_do_limiar_so_e_publicada_sozinha_depois_de_10_min(indice):
    relogio = Relogio(INICIO + timedelta(minutes=9, seconds=50))
    s = sessao_com_limiar(indice, Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]}), relogio, 0.9)

    s.recebe_fala("Lula", 560.0, FALA_DESEMPREGO)  # nota 0.92, ainda no aquecimento
    assert [lig["trecho"] for lig in s.fila()] == [DESEMPREGO["trecho"]]
    assert s.exporta()["ligacoes"] == []

    relogio.adianta(seconds=20)  # 10 min e 10 s desde o 00:00
    s.recebe_fala("Flávio", 590.0, FALA_PIX)  # Estadão 0.95 e AFP 0.9

    assert [lig["trecho"] for lig in s.fila()] == [DESEMPREGO["trecho"]]  # a primeira continua esperando
    dados = s.exporta()
    [lig] = dados["ligacoes"]
    assert (lig["trecho"], lig["origem"], lig["publicada_em"]) == (PIX["trecho"], "automatica",
                                                                    "2026-10-23T21:30:10-03:00")
    # na publicação automática vai o rótulo dos dados da agência, que a página mostra com o aviso
    assert {(c["agencia"], c["rotulo"], c["fonte_rotulo"]) for c in lig["checagens"]} == {
        ("Estadão", "Enganoso", "dados_da_agencia"), ("AFP Checamos", "Não é bem assim", "dados_da_agencia")}
    assert dados["meta"]["limiar_automatico"] == 0.9
    assert dados["meta"]["inicio_debate"] == "2026-10-23T21:20:00-03:00"


def test_repassando_a_gravacao_o_aquecimento_conta_no_relogio_da_gravacao(indice):
    # pós-debate: a sessão roda horas depois do 00:00, mas os 10 min de aquecimento são os do áudio
    relogio = Relogio(INICIO + timedelta(hours=3))
    s = Sessao(indice, chamar=Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]}), relogio=relogio,
               meta={"limiar_automatico": 0.9}, aquecimento_pela_gravacao=True)
    s.marca_inicio(INICIO)

    s.recebe_fala("Lula", 300.0, FALA_DESEMPREGO)  # nota 0.92, mas no minuto 5 da gravação
    s.recebe_fala("Flávio", 700.0, FALA_PIX)  # minuto 11 e pouco

    assert [lig["trecho"] for lig in s.fila()] == [DESEMPREGO["trecho"]]
    assert [lig["trecho"] for lig in s.exporta()["ligacoes"]] == [PIX["trecho"]]


def test_ligacao_abaixo_do_limiar_vai_para_a_fila_mesmo_depois_de_10_min(indice):
    relogio = Relogio(INICIO + timedelta(minutes=40))
    s = sessao_com_limiar(indice, Claude({FALA_DESEMPREGO: [DESEMPREGO]}), relogio, 0.95)

    s.recebe_fala("Lula", 2400.0, FALA_DESEMPREGO)  # nota 0.92

    assert len(s.fila()) == 1
    assert s.exporta()["ligacoes"] == []


def test_publicacao_automatica_leva_so_as_checagens_acima_do_limiar(indice):
    relogio = Relogio(INICIO + timedelta(minutes=40))
    s = sessao_com_limiar(indice, Claude({FALA_PIX: [PIX]}), relogio, 0.93)

    s.recebe_fala("Flávio", 2400.0, FALA_PIX)  # Estadão 0.95, AFP 0.9

    [lig] = s.exporta()["ligacoes"]
    assert [c["url"] for c in lig["checagens"]] == [PIX_ESTADAO]


def test_checagens_abaixo_do_limiar_numa_ligacao_publicada_sozinha_esperam_o_operador_na_fila(indice):
    relogio = Relogio(INICIO + timedelta(minutes=40))
    s = sessao_com_limiar(indice, Claude({FALA_PIX: [PIX]}), relogio, 0.93)

    s.recebe_fala("Flávio", 2400.0, FALA_PIX)  # Estadão 0.95 sai sozinha; AFP 0.9 fica

    [lig] = s.exporta()["ligacoes"]
    [resto] = s.fila()
    assert resto["id"] == lig["id"]
    assert [(c["url"], c["nota"]) for c in resto["checagens"]] == [(PIX_AFP, 0.9)]
    assert resto["nota"] == 0.9
    assert resto["ja_publicadas"] == [PIX_ESTADAO]


def test_aprovar_o_resto_de_uma_ligacao_publicada_sozinha_acrescenta_a_checagem_a_mesma_ligacao(indice):
    relogio = Relogio(INICIO + timedelta(minutes=40))
    s = sessao_com_limiar(indice, Claude({FALA_PIX: [PIX]}), relogio, 0.93)
    s.recebe_fala("Flávio", 2400.0, FALA_PIX)
    [resto] = s.fila()
    relogio.adianta(minutes=2)

    s.aprova(resto["id"], selos={PIX_AFP: "Falso"})

    assert s.fila() == []
    [lig] = s.exporta()["ligacoes"]
    assert (lig["id"], lig["origem"], lig["publicada_em"]) == (resto["id"], "automatica", "2026-10-23T22:00:00-03:00")
    # cada checagem diz como entrou: a da AFP foi aprovada por uma pessoa, não publicada sozinha
    assert [(c["url"], c["rotulo"], c["fonte_rotulo"], c["origem"]) for c in lig["checagens"]] == [
        (PIX_ESTADAO, "Enganoso", "dados_da_agencia", "automatica"), (PIX_AFP, "Falso", "selo_da_pagina", "aprovada")]
    assert valida(s.exporta()) == []
    with pytest.raises(ValueError):
        s.aprova(resto["id"])

    copia = Sessao.reconstroi(indice, json.loads(json.dumps(s.eventos)), chamar=Claude({}), relogio=relogio,
                              meta={"limiar_automatico": 0.93})
    assert estado(copia) == estado(s)


def test_recusar_o_resto_de_uma_ligacao_publicada_sozinha_mantem_a_ligacao_so_com_o_que_saiu(indice):
    relogio = Relogio(INICIO + timedelta(minutes=40))
    s = sessao_com_limiar(indice, Claude({FALA_PIX: [PIX]}), relogio, 0.93)
    s.recebe_fala("Flávio", 2400.0, FALA_PIX)
    [resto] = s.fila()

    s.recusa(resto["id"], "a da AFP trata de outra coisa")

    assert s.fila() == []
    [lig] = s.exporta()["ligacoes"]
    assert [c["url"] for c in lig["checagens"]] == [PIX_ESTADAO]
    [recusada] = s.recusadas()
    assert (recusada["id"], recusada["motivo_recusa"]) == (resto["id"], "a da AFP trata de outra coisa")
    assert [c["url"] for c in recusada["checagens"]] == [PIX_AFP]
    assert s.exporta()["correcoes"] == []

    copia = Sessao.reconstroi(indice, json.loads(json.dumps(s.eventos)), chamar=Claude({}), relogio=relogio,
                              meta={"limiar_automatico": 0.93})
    assert estado(copia) == estado(s)


def test_ligacao_ja_no_painel_nao_se_edita_pela_fila(indice):
    # o que já está publicado só muda por correção pública (despublicar e propor de novo)
    relogio = Relogio(INICIO + timedelta(minutes=40))
    s = sessao_com_limiar(indice, Claude({FALA_PIX: [PIX]}), relogio, 0.93)
    s.recebe_fala("Flávio", 2400.0, FALA_PIX)  # Estadão saiu sozinha; AFP espera na fila
    [resto] = s.fila()

    with pytest.raises(ValueError):
        s.edita(resto["id"], reformulacao="Outra reformulação.")


def test_despublicar_uma_ligacao_publicada_sozinha_tira_o_resto_dela_da_fila(indice):
    relogio = Relogio(INICIO + timedelta(minutes=40))
    s = sessao_com_limiar(indice, Claude({FALA_PIX: [PIX]}), relogio, 0.93)
    s.recebe_fala("Flávio", 2400.0, FALA_PIX)
    [resto] = s.fila()

    s.despublica(resto["id"], "Ligação errada.")

    assert s.fila() == []
    with pytest.raises(ValueError):
        s.aprova(resto["id"])


def test_sem_00_00_marcado_nada_e_publicado_sozinho(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=Relogio(INICIO + timedelta(hours=1)),
               meta={"limiar_automatico": 0.5})

    s.recebe_fala("Flávio", 3600.0, FALA_PIX)

    assert len(s.fila()) == 1
    assert s.exporta()["ligacoes"] == []


def test_com_a_publicacao_automatica_desligada_tudo_vai_para_a_fila(indice):
    relogio = Relogio(INICIO + timedelta(minutes=50))
    s = sessao_com_limiar(indice, Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX]}), relogio, None)

    s.recebe_fala("Lula", 2900.0, FALA_DESEMPREGO)
    s.recebe_fala("Flávio", 2950.0, FALA_PIX)

    assert [lig["trecho"] for lig in s.fila()] == [DESEMPREGO["trecho"], PIX["trecho"]]
    dados = s.exporta()
    assert dados["ligacoes"] == []
    assert dados["meta"]["limiar_automatico"] is None


def grava_limiar(dados, **campos):
    (dados / "avaliacao").mkdir(exist_ok=True)
    (dados / "avaliacao" / "limiar.json").write_text(
        json.dumps({"origem": "avaliação da Band", "em": "2026-10-22T10:00:00-03:00", **campos}), encoding="utf-8")


def test_limiar_vem_do_resultado_da_avaliacao_e_sem_ele_fica_desligado(tmp_path):
    from sessao import le_limiar

    limiar, motivo = le_limiar(tmp_path, modelo="claude-sonnet-5-5")  # sem arquivo
    assert limiar is None and "avaliação" in motivo
    grava_limiar(tmp_path, limiar=None, modelo="claude-sonnet-5-5")
    assert le_limiar(tmp_path, modelo="claude-sonnet-5-5")[0] is None
    grava_limiar(tmp_path, limiar=0.85, modelo="claude-sonnet-5-5")
    assert le_limiar(tmp_path, modelo="claude-sonnet-5-5") == (0.85, None)


def test_limiar_medido_com_outro_modelo_desliga_a_publicacao_automatica_e_diz_por_que(tmp_path):
    from sessao import le_limiar

    grava_limiar(tmp_path, limiar=0.85, modelo="claude-opus-5-5")

    limiar, motivo = le_limiar(tmp_path, modelo="claude-sonnet-5-5")

    assert limiar is None
    assert "claude-opus-5-5" in motivo and "claude-sonnet-5-5" in motivo


def test_limiar_sem_o_modelo_da_avaliacao_desliga_a_publicacao_automatica(tmp_path):
    from sessao import le_limiar

    grava_limiar(tmp_path, limiar=0.85)  # limiar.json de antes de o modelo ser gravado

    limiar, motivo = le_limiar(tmp_path, modelo="claude-sonnet-5-5")

    assert limiar is None and "modelo" in motivo


def test_despublicar_tira_a_ligacao_da_lista_e_cria_a_correcao_com_horario_e_motivo(indice):
    relogio = Relogio(INICIO + timedelta(minutes=30))
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX], FALA_DESEMPREGO: [DESEMPREGO]}), relogio=relogio)
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)
    s.recebe_fala("Lula", 1850.0, FALA_DESEMPREGO)
    pix, desemprego = (p["id"] for p in s.fila())
    s.aprova(pix, selos={PIX_ESTADAO: "Falso"})
    s.aprova(desemprego)
    [publicada] = [lig for lig in s.exporta()["ligacoes"] if lig["id"] == pix]
    relogio.adianta(minutes=3)

    s.despublica(pix, "A checagem da AFP trata da taxa do Pix, não da criação.")

    dados = s.exporta()
    assert [lig["id"] for lig in dados["ligacoes"]] == [desemprego]
    assert dados["correcoes"] == [{"tipo": "despublicacao", "em": "2026-10-23T21:53:00-03:00", "ligacao_id": pix,
                                   "motivo": "A checagem da AFP trata da taxa do Pix, não da criação.",
                                   "retirado": publicada}]
    assert valida(dados) == []


def test_despublicar_sem_motivo_nao_vale(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=relogio)
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)
    ident = s.fila()[0]["id"]
    s.aprova(ident)
    n = len(s.eventos)

    with pytest.raises(ValueError):
        s.despublica(ident, "  ")

    assert len(s.eventos) == n
    assert len(s.exporta()["ligacoes"]) == 1


def test_recusar_tira_da_fila_sem_publicar_e_nao_deixa_aprovar_depois(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=relogio)
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)
    ident = s.fila()[0]["id"]

    s.recusa(ident, "não é a mesma afirmação")

    assert s.fila() == []
    assert [p["id"] for p in s.recusadas()] == [ident]
    assert (s.exporta()["ligacoes"], s.exporta()["correcoes"]) == ([], [])
    with pytest.raises(ValueError):
        s.aprova(ident)


def test_corrigir_rotulo_de_ligacao_publicada_troca_o_rotulo_exibido_pelo_selo(indice):
    relogio = Relogio(INICIO + timedelta(minutes=40))
    s = sessao_com_limiar(indice, Claude({FALA_PIX: [PIX]}), relogio, 0.8)
    s.recebe_fala("Flávio", 2400.0, FALA_PIX)  # publicada sozinha com o rótulo do ClaimReview
    [lig] = s.exporta()["ligacoes"]

    s.corrige_rotulo(lig["id"], PIX_ESTADAO, "Falso")

    [lig] = s.exporta()["ligacoes"]
    assert {c["url"]: (c["rotulo"], c["fonte_rotulo"]) for c in lig["checagens"]} == {
        PIX_ESTADAO: ("Falso", "selo_da_pagina"), PIX_AFP: ("Não é bem assim", "dados_da_agencia")}
    assert lig["origem"] == "automatica"


def test_corrigir_rotulo_de_ligacao_publicada_entra_nas_correcoes_com_o_rotulo_antigo_e_o_novo(indice):
    relogio = Relogio(INICIO + timedelta(minutes=40))
    s = sessao_com_limiar(indice, Claude({FALA_PIX: [PIX]}), relogio, 0.8)
    s.recebe_fala("Flávio", 2400.0, FALA_PIX)
    [lig] = s.exporta()["ligacoes"]
    relogio.adianta(minutes=1)
    s.corrige_rotulo(lig["id"], PIX_ESTADAO, "Falso")
    relogio.adianta(minutes=1)

    s.corrige_rotulo(lig["id"], PIX_ESTADAO, "Enganoso")  # o operador errou o selo e corrige de novo

    dados = s.exporta()
    assert dados["correcoes"] == [
        {"tipo": "rotulo", "em": "2026-10-23T22:01:00-03:00", "ligacao_id": lig["id"], "minutagem_s": 2400.0,
         "quem": "Flávio", "agencia": "Estadão", "url": PIX_ESTADAO,
         "rotulo_anterior": "Enganoso", "fonte_rotulo_anterior": "dados_da_agencia",
         "rotulo": "Falso", "fonte_rotulo": "selo_da_pagina"},
        {"tipo": "rotulo", "em": "2026-10-23T22:02:00-03:00", "ligacao_id": lig["id"], "minutagem_s": 2400.0,
         "quem": "Flávio", "agencia": "Estadão", "url": PIX_ESTADAO,
         "rotulo_anterior": "Falso", "fonte_rotulo_anterior": "selo_da_pagina",
         "rotulo": "Enganoso", "fonte_rotulo": "selo_da_pagina"},
    ]
    assert [lig["id"] for lig in dados["ligacoes"]] == [lig["id"]]  # a ligação continua publicada
    assert valida(dados) == []


def test_corrigir_para_o_mesmo_selo_que_ja_esta_no_painel_nao_vale(indice):
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=relogio)
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)
    ident = s.fila()[0]["id"]
    s.aprova(ident, selos={PIX_ESTADAO: "Falso"})
    n = len(s.eventos)

    with pytest.raises(ValueError):
        s.corrige_rotulo(ident, PIX_ESTADAO, " Falso ")

    assert len(s.eventos) == n
    assert s.exporta()["correcoes"] == []


def test_o_publicador_e_avisado_a_cada_mudanca_do_conteudo_publico(indice):
    avisos = []
    relogio = Relogio(INICIO + timedelta(minutes=30))
    s = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=relogio, ao_mudar_publicado=avisos.append)

    s.recebe_fala("Mediação", 1790.0, FALA_MEDIACAO)  # não muda nada do que é público
    assert avisos == []
    s.recebe_fala("Flávio", 1800.0, FALA_PIX)  # proposta: só na fila; o método passa a citar o modelo
    [dados] = avisos
    assert (dados["ligacoes"], dados["meta"]["modelos_que_responderam"]) == ([], ["dublê"])
    s.recebe_fala("Flávio", 1850.0, FALA_PIX)  # outra proposta do mesmo modelo: nada público muda
    assert len(avisos) == 1
    s.aprova(s.fila()[0]["id"])

    assert len(avisos) == 2
    assert [lig["trecho"] for lig in avisos[-1]["ligacoes"]] == [PIX["trecho"]]


def test_exportacao_lista_as_agencias_pela_politica_de_agencias(indice):
    from agencias import agencias_do_painel

    esperado = agencias_do_painel(indice.registros)
    assert {"Lupa", "Observador"} <= {a["nome"] for a in esperado["agencias_excluidas"]}

    meta = sessao(indice, Claude({})).exporta()["meta"]

    assert meta["agencias_incluidas"] == esperado["agencias_incluidas"]
    assert meta["agencias_excluidas"] == esperado["agencias_excluidas"]
    assert {"nome": "Estadão", "via": "ClaimReview na API do Google"} in meta["agencias_incluidas"]


# ---------- retomada ----------

def noite_movimentada(indice, relogio, ao_registrar=None, claude=None):
    """Falas, publicação automática, aprovação com selo, recusa e despublicação."""
    gasolina = afirmacao("a gasolina caiu trinta por cento no posto em um ano",
                         "O preço da gasolina caiu 30% em um ano no governo Bolsonaro.", "numero")
    claude = claude or Claude({FALA_DESEMPREGO: [DESEMPREGO], FALA_PIX: [PIX], FALA_GASOLINA: [gasolina],
                               FALA_LICENCA: [afirmacao(
                                   "você foi o único deputado da Assembleia que votou contra a licença-maternidade",
                                   "Flávio Bolsonaro votou contra a licença-maternidade de seis meses na Alerj.",
                                   "perene", {LICENCA_2022: (0.7, "Mesmo voto.")})]})
    s = Sessao(indice, chamar=claude, relogio=relogio, ao_registrar=ao_registrar, meta={"limiar_automatico": 0.94})
    s.marca_inicio(INICIO)
    s.recebe_fala("Mediação", 0.0, FALA_MEDIACAO)
    s.recebe_fala("Lula", 300.0, FALA_DESEMPREGO)  # aquecimento: fila
    relogio.adianta(minutes=15)
    s.recebe_fala("Flávio", 900.0, FALA_PIX)  # Estadão 0.95: sai sozinha; AFP 0.9 fica na fila
    s.recebe_fala("Flávio", 950.0, FALA_GASOLINA)  # sem par
    s.recebe_fala("Lula", 1000.0, FALA_LICENCA)  # 0.7: fila
    desemprego, licenca = (p["id"] for p in s.fila() if not p["ja_publicadas"])
    s.aprova(desemprego, selos={DESEMPREGO_2025: "Verdadeiro, mas"})
    s.recusa(licenca)
    pix = next(lig["id"] for lig in s.exporta()["ligacoes"] if lig["origem"] == "automatica")
    s.despublica(pix, "Ligação errada.")  # o resto do Pix sai da fila junto
    assert s.fila() == []
    return s


def estado(s):
    return s.fila(), s.recusadas(), s.descartados(), s.erros(), s.exporta()


def test_repetir_o_registro_de_eventos_reconstroi_o_mesmo_estado_sem_chamar_o_claude(indice):
    relogio = Relogio(INICIO)
    original = noite_movimentada(indice, relogio)
    assert len(original.exporta()["correcoes"]) == 1  # a noite teve de tudo
    claude = Claude({})

    copia = Sessao.reconstroi(indice, json.loads(json.dumps(original.eventos)), chamar=claude, relogio=relogio,
                              meta={"limiar_automatico": 0.94})

    assert claude.chamadas == []
    assert estado(copia) == estado(original)
    assert copia.eventos == original.eventos  # nada republicado nem registrado de novo


def test_ligacao_tardia_segue_a_politica_de_publicacao_e_o_registro_a_reconstroi_sem_chamar_o_claude():
    registros = json.loads((FIXTURES / "base_sessao.json").read_text(encoding="utf-8"))["registros"]
    corte = afirmacao("vai cortar o décimo terceiro e as aposentadorias",
                      "Flávio Bolsonaro disse que, se eleito, vai cortar o 13º salário e as aposentadorias.",
                      "perene", {FOF_13: (0.95, "Mesma fala atribuída a Flávio.")})
    relogio = Relogio(INICIO + timedelta(minutes=25))
    s = sessao_com_limiar(Indice(registros), Claude({FALA_13: [corte]}), relogio, 0.9)
    s.recebe_fala("Lula", 1500.0, FALA_13)
    relogio.adianta(minutes=10)

    s.atualiza_base([CHECAGEM_DA_NOITE])

    [lig] = s.exporta()["ligacoes"]  # nota 0.95 ≥ limiar, depois dos 10 min: sai sozinha
    assert (lig["trecho"], lig["origem"], lig["ligada_depois"]) == (corte["trecho"], "automatica", True)
    assert s.exporta()["sem_par"] == []

    claude = Claude({})
    copia = Sessao.reconstroi(Indice(registros), json.loads(json.dumps(s.eventos)), chamar=claude,
                              relogio=relogio, meta={"limiar_automatico": 0.9})

    assert claude.chamadas == []
    assert estado(copia) == estado(s)
    assert FOF_13 in {r["id"] for r in copia.indice.registros}  # a base em memória volta com a checagem nova


def test_sessao_gravada_em_pasta_retoma_depois_de_um_travamento(indice, tmp_path):
    relogio = Relogio(INICIO)
    original = noite_movimentada(indice, relogio)
    gravada = Sessao.abre(tmp_path, indice, chamar=original._chamar, relogio=Relogio(INICIO),
                          meta={"limiar_automatico": 0.94})
    for e in original.eventos:  # o mesmo registro, gravado evento a evento
        gravada._ao_registrar(e)
    with (tmp_path / "eventos.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"n": 999, "tipo": "fala", "fa')  # travou no meio da gravação de um evento
    claude = Claude({FALA_PIX: [PIX]})

    retomada = Sessao.abre(tmp_path, indice, chamar=claude, relogio=relogio, meta={"limiar_automatico": 0.94})

    assert claude.chamadas == []
    assert estado(retomada) == estado(original)
    # e segue gravando no mesmo registro
    retomada.recebe_fala("Flávio", 1500.0, FALA_PIX)
    de_novo = Sessao.abre(tmp_path, indice, chamar=Claude({}), relogio=relogio, meta={"limiar_automatico": 0.94})
    assert estado(de_novo) == estado(retomada)
    assert len(de_novo.exporta()["ligacoes"]) == 2


def test_fala_registrada_e_nao_analisada_antes_do_travamento_e_analisada_ao_retomar(indice):
    relogio = Relogio(INICIO + timedelta(minutes=30))
    original = Sessao(indice, chamar=Claude({FALA_PIX: [PIX]}), relogio=relogio)
    original.recebe_fala("Flávio", 1800.0, FALA_PIX)
    [fala] = original.eventos[:1]  # o processo morreu logo depois de registrar a fala
    claude = Claude({FALA_PIX: [PIX], FALA_DESEMPREGO: [DESEMPREGO]})

    retomada = Sessao.reconstroi(indice, [fala], chamar=claude, relogio=relogio)
    assert retomada.fila() == [] and claude.chamadas == []
    retomada.retoma_analises()

    assert [p["trecho"] for p in retomada.fila()] == [PIX["trecho"]]
    assert claude.extracoes() == 1
    retomada.retoma_analises()  # nada mais pendente
    assert claude.extracoes() == 1


# ---------- falhas do Claude: novas tentativas na noite e depois de reabrir ----------

class ClaudeInstavel(Claude):
    """Dublê que falha (como a API sobrecarregada) as primeiras `falhas` chamadas de cada pedido dado:
    falhas = {("extracao" | "confirmacao", texto que aparece no pedido): n}."""

    def __init__(self, falas, falhas):
        super().__init__(falas)
        self.falhas = dict(falhas)

    def __call__(self, etapa, prompt, conteudo, schema):
        for (tipo, texto), n in self.falhas.items():
            if pedido(schema) == tipo and texto in conteudo and n:
                self.falhas[(tipo, texto)] = n - 1
                self.chamadas.append(pedido(schema))
                raise ConnectionError("529 overloaded")
        return super().__call__(etapa, prompt, conteudo, schema)


SEM_ESPERA = (0, 0)  # três tentativas, sem esperar entre elas


def test_falha_passageira_do_claude_e_tentada_de_novo_e_a_fala_e_analisada(indice):
    claude = ClaudeInstavel({FALA_PIX: [PIX]}, {("extracao", FALA_PIX): 1, ("confirmacao", PIX["trecho"]): 1})
    s = Sessao(indice, chamar=claude, relogio=relogio, esperas_entre_tentativas=SEM_ESPERA)

    s.recebe_fala("Flávio", 400.0, FALA_PIX)

    assert [p["trecho"] for p in s.fila()] == [PIX["trecho"]]
    assert [e["etapa"] for e in s.erros()] == ["extrair", "confirmar"]  # cada falha aparece na tela
    assert (claude.extracoes(), claude.confirmacoes()) == (2, 2)


def test_falha_que_insiste_para_depois_de_algumas_tentativas_e_cada_uma_aparece_nos_erros(indice):
    claude = ClaudeInstavel({FALA_PIX: [PIX]}, {("extracao", FALA_PIX): 99})
    s = Sessao(indice, chamar=claude, relogio=relogio, esperas_entre_tentativas=SEM_ESPERA)

    s.recebe_fala("Flávio", 400.0, FALA_PIX)

    assert s.fila() == []
    assert claude.extracoes() == 3
    assert [(e["etapa"], e["quem"], e["minutagem_s"]) for e in s.erros()] == [("extrair", "Flávio", 400.0)] * 3


def test_extracao_que_falhou_em_todas_as_tentativas_e_refeita_ao_reabrir_e_so_ela_volta_ao_claude(indice):
    instavel = ClaudeInstavel({FALA_PIX: [PIX], FALA_DESEMPREGO: [DESEMPREGO]}, {("extracao", FALA_PIX): 99})
    original = Sessao(indice, chamar=instavel, relogio=relogio, esperas_entre_tentativas=SEM_ESPERA)
    original.recebe_fala("Lula", 754.3, FALA_DESEMPREGO)
    original.recebe_fala("Flávio", 800.0, FALA_PIX)
    claude = Claude({FALA_PIX: [PIX], FALA_DESEMPREGO: [DESEMPREGO]})

    reaberta = Sessao.reconstroi(indice, json.loads(json.dumps(original.eventos)), chamar=claude, relogio=relogio)
    assert len(reaberta.erros()) == 3  # o que falhou continua na tela
    reaberta.retoma_analises()

    assert [p["trecho"] for p in reaberta.fila()] == [DESEMPREGO["trecho"], PIX["trecho"]]
    assert (claude.extracoes(), claude.confirmacoes()) == (1, 1)  # só a do Pix: o desemprego já estava feito
    de_novo = Sessao.reconstroi(indice, json.loads(json.dumps(reaberta.eventos)), chamar=Claude({}), relogio=relogio)
    de_novo.retoma_analises()
    assert estado(de_novo) == estado(reaberta)


def test_confirmacao_que_falhou_em_todas_as_tentativas_e_refeita_ao_reabrir_sem_refazer_a_extracao(indice):
    instavel = ClaudeInstavel({FALA_PIX: [PIX]}, {("confirmacao", PIX["trecho"]): 99})
    original = Sessao(indice, chamar=instavel, relogio=relogio, esperas_entre_tentativas=SEM_ESPERA)
    original.recebe_fala("Flávio", 800.0, FALA_PIX)
    assert original.fila() == [] and original.exporta()["sem_par"] == []
    claude = Claude({FALA_PIX: [PIX]})

    reaberta = Sessao.reconstroi(indice, json.loads(json.dumps(original.eventos)), chamar=claude, relogio=relogio)
    reaberta.retoma_analises()

    assert [p["trecho"] for p in reaberta.fila()] == [PIX["trecho"]]
    assert (claude.extracoes(), claude.confirmacoes()) == (0, 1)


def test_analise_que_falhou_em_todas_as_tentativas_e_refeita_na_mesma_noite_sem_reabrir(indice):
    # a API fora do ar por mais tempo que as tentativas: a fala não pode ficar sem análise até alguém
    # fechar e reabrir a janela (o que perde o áudio desse intervalo)
    claude = ClaudeInstavel({FALA_PIX: [PIX], FALA_DESEMPREGO: [DESEMPREGO]},
                            {("extracao", FALA_PIX): 3, ("confirmacao", DESEMPREGO["trecho"]): 3})
    s = Sessao(indice, chamar=claude, relogio=relogio, esperas_entre_tentativas=SEM_ESPERA)
    s.recebe_fala("Flávio", 800.0, FALA_PIX)
    s.recebe_fala("Lula", 900.0, FALA_DESEMPREGO)
    assert s.fila() == [] and s.falhas_pendentes() == 2

    s.refaz_falhas()

    assert sorted(p["trecho"] for p in s.fila()) == sorted([PIX["trecho"], DESEMPREGO["trecho"]])
    assert s.falhas_pendentes() == 0
    assert (claude.extracoes(), claude.confirmacoes()) == (3 + 1 + 1, 1 + 3 + 1)  # nada repetido além da falha
    s.refaz_falhas()  # sem falhas pendentes, não chama o Claude
    assert (claude.extracoes(), claude.confirmacoes()) == (5, 5)


def test_falha_que_continua_volta_para_as_pendentes(indice):
    claude = ClaudeInstavel({FALA_PIX: [PIX]}, {("extracao", FALA_PIX): 99})
    s = Sessao(indice, chamar=claude, relogio=relogio, esperas_entre_tentativas=SEM_ESPERA)
    s.recebe_fala("Flávio", 800.0, FALA_PIX)

    s.refaz_falhas()

    assert s.falhas_pendentes() == 1 and claude.extracoes() == 6


def test_reconferencia_da_base_que_falhou_e_refeita_ao_reabrir():
    registros = json.loads((FIXTURES / "base_sessao.json").read_text(encoding="utf-8"))["registros"]
    corte = afirmacao("vai cortar o décimo terceiro e as aposentadorias",
                      "Flávio Bolsonaro disse que, se eleito, vai cortar o 13º salário e as aposentadorias.",
                      "perene", {FOF_13: (0.9, "Mesma fala atribuída a Flávio.")})
    instavel = ClaudeInstavel({FALA_13: [corte]}, {})
    original = Sessao(Indice(registros), chamar=instavel, relogio=relogio, esperas_entre_tentativas=SEM_ESPERA)
    original.recebe_fala("Lula", 1500.0, FALA_13)
    instavel.falhas[("confirmacao", corte["trecho"])] = 99  # a API cai bem na atualização da base
    original.atualiza_base([CHECAGEM_DA_NOITE])
    assert original.fila() == [] and len(original.erros()) == 3
    claude = Claude({FALA_13: [corte]})

    reaberta = Sessao.reconstroi(Indice(registros), json.loads(json.dumps(original.eventos)), chamar=claude,
                                 relogio=relogio)
    reaberta.retoma_analises()

    [lig] = reaberta.fila()
    assert (lig["trecho"], lig["ligada_depois"]) == (corte["trecho"], True)
    assert claude.confirmacoes() == 1


GASOLINA_NOITE_URL = "https://g1.globo.com/fato-ou-fake/noticia/2026/10/23/gasolina-caiu-30-por-cento.ghtml"
GASOLINA_NOITE = {**CHECAGEM_DA_NOITE, "id": GASOLINA_NOITE_URL, "url": GASOLINA_NOITE_URL,
                  "frase": "Gasolina caiu 30% em um ano no governo Bolsonaro", "rotulo": "#NÃOÉBEMASSIM"}


def test_atualizacao_da_base_interrompida_no_meio_termina_ao_retomar_sem_refazer_o_que_ja_foi_conferido():
    registros = json.loads((FIXTURES / "base_sessao.json").read_text(encoding="utf-8"))["registros"]
    corte = afirmacao("vai cortar o décimo terceiro e as aposentadorias",
                      "Flávio Bolsonaro disse que, se eleito, vai cortar o 13º salário e as aposentadorias.",
                      "perene", {FOF_13: (0.9, "Mesma fala atribuída a Flávio.")})
    gasolina = afirmacao("a gasolina caiu trinta por cento no posto em um ano",
                         "O preço da gasolina caiu 30% em um ano no governo Bolsonaro.", "numero",
                         {GASOLINA_NOITE_URL: (0.85, "Mesmo número.")})
    relogio = Relogio(INICIO + timedelta(minutes=30))
    claude_original = Claude({FALA_13: [corte], FALA_GASOLINA: [gasolina]})
    original = Sessao(Indice(registros), chamar=claude_original, relogio=relogio)
    original.recebe_fala("Lula", 1500.0, FALA_13)
    original.recebe_fala("Flávio", 1530.0, FALA_GASOLINA)
    original.atualiza_base([CHECAGEM_DA_NOITE, GASOLINA_NOITE])
    assert [p["trecho"] for p in original.fila()] == [corte["trecho"], gasolina["trecho"]]
    # o processo morreu depois de registrar a base e a primeira reconferência
    n = next(i for i, e in enumerate(original.eventos) if e.get("tardia")) + 1
    claude = Claude({FALA_13: [corte], FALA_GASOLINA: [gasolina]})

    retomada = Sessao.reconstroi(Indice(registros), json.loads(json.dumps(original.eventos[:n])), chamar=claude,
                                 relogio=relogio)
    assert [p["trecho"] for p in retomada.fila()] == [corte["trecho"]]
    retomada.retoma_analises()

    assert claude.confirmacoes() == 1  # só a gasolina volta ao Claude
    assert estado(retomada) == estado(original)
    retomada.retoma_analises()  # nada mais pendente
    assert claude.confirmacoes() == 1
    feitas = len(claude_original.chamadas)
    original.retoma_analises()  # a atualização que terminou não é refeita
    assert len(claude_original.chamadas) == feitas


def test_ligacao_tardia_so_chama_o_claude_para_a_afirmacao_que_teria_a_checagem_nova_entre_as_candidatas(monkeypatch):
    # a base de exemplo tem só 20 checagens: com 8 candidatas, quase toda checagem nova entraria entre elas
    monkeypatch.setattr(sessao_mod, "N_CANDIDATAS", 2)
    registros = json.loads((FIXTURES / "base_sessao.json").read_text(encoding="utf-8"))["registros"]
    licenca = afirmacao("votou contra a licença-maternidade de seis meses",
                        "Flávio Bolsonaro votou contra a licença-maternidade de seis meses na Alerj em 2009.", "perene")
    gasolina = afirmacao("a gasolina caiu trinta por cento no posto em um ano",
                         "O preço da gasolina caiu 30% em um ano no governo Bolsonaro.", "numero",
                         {GASOLINA_NOITE_URL: (0.85, "Mesmo número.")})
    claude = Claude({FALA_LICENCA: [licenca], FALA_GASOLINA: [gasolina]})
    s = Sessao(Indice(registros), chamar=claude, relogio=relogio)
    s.recebe_fala("Lula", 1500.0, FALA_LICENCA)
    s.recebe_fala("Flávio", 1530.0, FALA_GASOLINA)
    assert len(s.exporta()["sem_par"]) == 2
    antes = claude.confirmacoes()

    s.atualiza_base([GASOLINA_NOITE])

    # a checagem da gasolina não estaria entre as candidatas da licença: só a gasolina volta ao Claude
    assert claude.confirmacoes() == antes + 1
    [lig] = s.fila()
    assert (lig["trecho"], lig["ligada_depois"]) == (gasolina["trecho"], True)
    assert [a["trecho"] for a in s.exporta()["sem_par"]] == [licenca["trecho"]]
