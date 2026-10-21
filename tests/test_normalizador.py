"""Normalizador (ponto de teste 2): resposta bruta de cada fonte → registros de checagem.

Os exemplos fixos são recortes de respostas reais (só metadados) em tests/fixtures/.
"""
import json
from collections import Counter
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


# ---------- Fato ou Fake (g1): RSS da seção e páginas achadas no sitemap ----------
# recortes das amostras reais, sem o corpo das checagens (tests/fixtures/g1_rss_fof.xml e g1_check.html)

FOF_FATO = ("https://g1.globo.com/fato-ou-fake/noticia/2026/10/20/"
            "e-fato-video-mostra-turistas-empurrando-malas-em-meio-a-protestos-em-paris-assista.ghtml")
FOF_PAGINA = ("https://g1.globo.com/fato-ou-fake/noticia/2025/06/11/e-fake-video-em-que-bolsonaro-diz-se-recusar-"
              "a-estar-inelegivel-gravacao-foi-manipulada-com-inteligencia-artificial.ghtml")


def fato_ou_fake(nome):
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_rss_do_fato_ou_fake_extrai_os_campos_da_checagem():
    registros = normaliza("fato_ou_fake_rss", fato_ou_fake("g1_rss_fof.xml"), COLETA)
    assert por_url(registros)[FOF_FATO] == {
        "id": FOF_FATO,
        "url": FOF_FATO,
        "agencia": "Fato ou Fake (g1)",
        "agencia_na_fonte": "Fato ou Fake (g1)",
        "site": "g1.globo.com",
        "frase": "Vídeo mostra turistas empurrando malas em meio a protestos em Paris; assista",
        "quem_disse": None,
        "idioma": "pt",
        "data": "2026-10-20",
        "data_origem": "publicacao",
        "rotulo": "#FATO",
        "coletado_em": COLETA,
        "fonte_coleta": "fato_ou_fake_rss",
        "buscas": [],
    }


def test_rotulo_do_fato_ou_fake_sai_do_selo_no_titulo():
    # 100 itens: 56 "É #FAKE", 3 "É #FATO", 1 "É FATO" sem o #, 1 "#NÃOÉBEMASSIM", 36 resumos
    # "Veja o que é #FATO ou #FAKE…" e 3 notícias sem selo
    registros = normaliza("fato_ou_fake_rss", fato_ou_fake("g1_rss_fof.xml"), COLETA)
    assert Counter(r["rotulo"] for r in registros) == {"#FAKE": 56, "#FATO": 4, "#NÃOÉBEMASSIM": 1}
    frases = {r["rotulo"]: r["frase"] for r in registros if r["rotulo"] != "#FAKE"}
    assert frases["#NÃOÉBEMASSIM"] == ("Bolsa Família teve reajuste maior que do salário mínimo em 2026, "
                                       "mas não supera aumento dos últimos três anos")
    assert "Que Lula disse 'mulher é que nem milho'" not in {r["frase"] for r in registros}
    assert "Lula disse 'mulher é que nem milho'" in {r["frase"] for r in registros}
    assert not [r for r in registros if "Veja o que é" in r["frase"] or "Fato ou Fake:" in r["frase"]]


def test_pagina_do_fato_ou_fake_achada_no_sitemap_extrai_os_campos_da_checagem():
    # o <title> e o og:title da página não têm o selo; o título com o selo é o da manchete (h1)
    [r] = normaliza("fato_ou_fake_pagina", fato_ou_fake("g1_check.html"), COLETA)
    assert r == {
        "id": FOF_PAGINA,
        "url": FOF_PAGINA,
        "agencia": "Fato ou Fake (g1)",
        "agencia_na_fonte": "Fato ou Fake (g1)",
        "site": "g1.globo.com",
        "frase": ("Vídeo em que Bolsonaro diz se recusar a 'estar inelegível'; gravação foi manipulada com "
                  "inteligência artificial"),
        "quem_disse": None,
        "idioma": "pt",
        "data": "2025-06-11",
        "data_origem": "publicacao",
        "rotulo": "#FAKE",
        "coletado_em": COLETA,
        "fonte_coleta": "fato_ou_fake_pagina",
        "buscas": [],
    }


def test_selo_do_fato_ou_fake_com_o_assunto_antes_ou_o_jogo_da_velha_trocado():
    # manchetes reais de 2025 (recorte guardado pela coleta)
    def pagina(titulo):
        return (f'<link rel="canonical" href="https://g1.globo.com/fato-ou-fake/noticia/2025/07/03/x.ghtml">\n'
                f'<h1 class="content-head__title" itemprop="headline">{titulo}</h1>\n')

    [jota] = normaliza("fato_ou_fake_pagina", pagina("Diogo Jota: É #FAKE que foto de carro queimado em rodovia "
                                                     "seja de acidente com jogador; imagem é de agosto de 2021"), COLETA)
    [raios] = normaliza("fato_ou_fake_pagina", pagina("#É FAKE vídeo que mostra sequência de raios intensos "
                                                      "&#x27;ejetando plasma&#x27; sobre praia; trata-se de IA"), COLETA)
    assert (jota["rotulo"], jota["frase"]) == ("#FAKE", "Foto de carro queimado em rodovia seja de acidente com "
                                                        "jogador; imagem é de agosto de 2021")
    assert (raios["rotulo"], raios["frase"]) == ("#FAKE", "Vídeo que mostra sequência de raios intensos "
                                                          "'ejetando plasma' sobre praia; trata-se de IA")
    assert normaliza("fato_ou_fake_pagina", pagina("Fato ou Fake: veja como enviar sugestões"), COLETA) == []
