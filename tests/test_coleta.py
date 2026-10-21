"""Coletor da base: o que cada adaptador guarda em brutos/ vira, ao refazer a base, os mesmos registros que
a resposta original daria, e nada do corpo das checagens fica guardado.

A rede é trocada por um cliente dublê que serve as amostras reais de tests/fixtures/ (o cliente do g1 é
passado ao coletor, como na coleta de verdade).
"""
import json
from pathlib import Path
from types import SimpleNamespace

from base_checagens import FOF_RSS, G1_SITEMAP, coleta_fof_rss, coleta_fof_sitemap, le_base, reconstroi
from checagens import normaliza

FIXTURES = Path(__file__).parent / "fixtures"
PAGINA_FOF = ("https://g1.globo.com/fato-ou-fake/noticia/2025/06/11/e-fake-video-em-que-bolsonaro-diz-se-recusar-"
              "a-estar-inelegivel-gravacao-foi-manipulada-com-inteligencia-artificial.ghtml")
DIA = "https://g1.globo.com/sitemap/g1/2025/06/11_1.xml"
SITEMAPS = ('<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f'<sitemap><loc>{DIA}</loc><lastmod>2025-06-11</lastmod></sitemap></sitemapindex>')
DO_DIA = ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          f'<url><loc>{PAGINA_FOF}</loc></url><url><loc>https://g1.globo.com/economia/noticia/outra.ghtml</loc></url>'
          '</urlset>')


def fixture(nome):
    return (FIXTURES / nome).read_text(encoding="utf-8")


class G1:
    """Cliente do g1 dublê: get(url) e cabeca(url), como base_checagens.G1, servindo `paginas`."""

    def __init__(self, paginas):
        self.paginas, self.pedidas = paginas, []

    def get(self, url, **kw):
        self.pedidas.append(url)
        texto = self.paginas.get(url)
        return None if texto is None else SimpleNamespace(text=texto, content=texto.encode("utf-8"), encoding=None)

    def cabeca(self, url):
        self.pedidas.append(url)
        return self.paginas.get(url)


def guardado(pasta):
    return "\n".join(p.read_text(encoding="utf-8") for p in (pasta / "brutos").glob("*/*.jsonl"))


def test_rss_do_fato_ou_fake_guarda_so_metadados_e_da_os_mesmos_registros(tmp_path):
    rss = fixture("g1_rss_fof.xml").replace("</title>", "</title><description>Corpo da checagem.</description>", 1)

    coleta_fof_rss(G1({FOF_RSS: rss}), tmp_path / "brutos" / "fato_ou_fake" / "coleta.jsonl")

    base = reconstroi(tmp_path)
    esperado = normaliza("fato_ou_fake_rss", rss, "x")
    assert [(r["url"], r["frase"], r["rotulo"], r["data"]) for r in base] == \
        [(r["url"], r["frase"], r["rotulo"], r["data"]) for r in esperado]
    assert "Corpo da checagem" not in guardado(tmp_path)


def test_pagina_achada_no_sitemap_guarda_so_metadados_e_da_o_mesmo_registro(tmp_path):
    pagina = fixture("g1_check.html")
    g1 = G1({G1_SITEMAP: SITEMAPS, DIA: DO_DIA, PAGINA_FOF: pagina})

    coleta_fof_sitemap(g1, tmp_path, tmp_path / "brutos" / "fato_ou_fake" / "coleta.jsonl", desde="2025-01-01",
                       log=lambda m: None)

    assert "https://g1.globo.com/economia/noticia/outra.ghtml" not in g1.pedidas  # só a seção
    [r] = reconstroi(tmp_path)
    [esperado] = normaliza("fato_ou_fake_pagina", pagina, "x")
    assert (r["url"], r["frase"], r["rotulo"], r["data"]) == (esperado["url"], esperado["frase"], esperado["rotulo"],
                                                              esperado["data"])
    texto = guardado(tmp_path)
    assert "Posts falsos manipularam" not in texto  # subtítulo da matéria
    assert len(json.loads(texto.splitlines()[0])["resposta"]) < 1000
    assert le_base(tmp_path) == [r]


# ---------- buscador interno do Fact Check Explorer: reserva de emergência, desligada por padrão ----------

VAZIA = ")]}'\n\n[[\"claims_response\"]]"


class Explorer:
    """O buscador interno do Explorer, dublê: a 1ª página de cada busca é a amostra real, a seguinte vem vazia."""

    def __init__(self):
        self.pedidos = []

    def get(self, url, params=None, timeout=None, **kw):
        self.pedidos.append((url, dict(params)))
        texto = fixture("gfce_flavio_p0.txt") if params["offset"] == 0 else VAZIA
        return SimpleNamespace(status_code=200, text=texto)


def test_explorer_de_emergencia_pagina_cada_busca_ate_vir_vazia_e_guarda_o_que_o_normalizador_le(tmp_path):
    from base_checagens import EXPLORER, coleta_explorer

    explorer = Explorer()

    coleta_explorer(["Flávio Bolsonaro"], tmp_path / "brutos" / "google_explorer" / "coleta.jsonl", http=explorer,
                    pausa=0, log=lambda m: None)

    assert [(u, p["query"], p["offset"]) for u, p in explorer.pedidos] == [
        (EXPLORER, "Flávio Bolsonaro", 0), (EXPLORER, "Flávio Bolsonaro", 10)]
    base = reconstroi(tmp_path)
    esperado = normaliza("google_explorer", fixture("gfce_flavio_p0.txt"), "x", busca="Flávio Bolsonaro")
    assert sorted(r["url"] for r in base) == sorted(r["url"] for r in esperado)
    assert {r["fonte_coleta"] for r in base} == {"google_explorer"}
    assert all(r["buscas"] == ["Flávio Bolsonaro"] for r in base)
