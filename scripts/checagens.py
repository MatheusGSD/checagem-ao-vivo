"""Base local de checagens: normalizador de cada fonte num registro uniforme (só metadados).

Um registro de checagem tem: id (URL canônica), url, agência (nome exibido e nome como a fonte
entregou) e site, frase checada, quem disse,
idioma, data e de onde ela veio (revisao, publicacao ou afirmacao), rótulo como a fonte entrega,
data e fonte da coleta e as buscas que o trouxeram. Nada do corpo do texto da checagem.
`normaliza` e `junta` são puras (sem rede); a coleta e o índice ficam em base_checagens.py.

Fontes:
  google_api       resposta JSON de claims:search da API oficial do Google Fact Check
  google_explorer  resposta do buscador interno do Fact Check Explorer (reserva, desligado por padrão)
  fato_ou_fake_rss     RSS da seção Fato ou Fake do g1 (rótulo = selo no começo do título)
  fato_ou_fake_pagina  página de uma checagem do Fato ou Fake achada no sitemap diário do g1
Do Fato ou Fake a coleta guarda só o `recorte` (URL, título com o selo e data), nunca o corpo.
"""
import html
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

from comum import BRASILIA

DATA_NA_URL = re.compile(r"/(20\d\d)/(\d\d)/(\d\d)/")


def _dia(iso):
    """'2026-10-19T15:31:00Z' → '2026-10-19' (data como a fonte entrega, em UTC)."""
    return iso[:10] if iso else None


def _dia_epoch(segundos):
    return datetime.fromtimestamp(segundos, timezone.utc).strftime("%Y-%m-%d") if segundos else None


def _data(revisao, url, publicacao=None, afirmacao=None):
    """Data da checagem e de onde veio. Muitos resultados vêm sem data de revisão (UOL, Estadão):
    aí vale a data de publicação (a da URL, como em .../2026/10/20/..., ou a que a fonte informar)
    e, por último, a data da afirmação checada."""
    m = DATA_NA_URL.search(url or "")
    for data, origem in [(revisao, "revisao"), (m and "-".join(m.groups()), "publicacao"),
                         (publicacao, "publicacao"), (afirmacao, "afirmacao")]:
        if data:
            return data, origem
    return None, None


def url_canonica(url):
    """Identificador estável da checagem: a URL com o host em minúsculas, sem fragmento e sem
    parâmetros de modelo não preenchidos (o BOL entrega '?t={seek_to_second_number}')."""
    u = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if "{" not in v]
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path, urlencode(query), ""))


def _registro(url, agencia, site, frase, quem_disse, idioma, data, rotulo, coletado_em, fonte, busca):
    return {
        "id": url_canonica(url),
        "url": url,
        "agencia": agencia,  # nome exibido; a política de agências (agencias.py) unifica grafias
        "agencia_na_fonte": agencia,  # como a fonte entregou, nunca alterado
        "site": site,
        "frase": frase,
        "quem_disse": quem_disse,
        "idioma": idioma,
        "data": data[0],
        "data_origem": data[1],
        "rotulo": rotulo,
        "coletado_em": coletado_em,
        "fonte_coleta": fonte,
        "buscas": [busca] if busca else [],
    }


def _google_api(resposta, coletado_em, busca):
    out = []
    for claim in resposta.get("claims", []):
        for rev in claim.get("claimReview", []):
            editor = rev.get("publisher", {})
            data = _data(_dia(rev.get("reviewDate")), rev["url"], afirmacao=_dia(claim.get("claimDate")))
            out.append(_registro(rev["url"], editor.get("name"), editor.get("site"), claim.get("text"),
                                 claim.get("claimant"), rev.get("languageCode"), data, rev.get("textualRating"),
                                 coletado_em, "google_api", busca))
    return out


def _google_explorer(texto, coletado_em, busca):
    """JSON posicional depois do prefixo anti-JSON `)]}'`: d[0] = ["claims_response", [entradas], ...];
    entrada = [claim, imagem, nota]; claim = [frase, [quem disse, ...], data da afirmação (epoch s),
    [reviews]]; review = [[agência, site, ...], url, revisão (epoch s ou None), rótulo, _, _, idioma,
    países, título, _, _, publicação (epoch s)]."""
    topo = json.loads(texto[texto.index("\n"):])[0]
    out = []
    for entrada in (topo[1] if len(topo) > 1 else None) or []:
        claim = entrada[0]
        quem = (claim[1] or [None])[0]
        for rev in claim[3] or []:
            data = _data(_dia_epoch(rev[2]), rev[1], publicacao=_dia_epoch(rev[11]), afirmacao=_dia_epoch(claim[2]))
            out.append(_registro(rev[1], rev[0][0], rev[0][1], claim[0], quem, rev[6], data, rev[3],
                                 coletado_em, "google_explorer", busca))
    return out


# ---------- Fato ou Fake (g1) ----------

FOF_AGENCIA = "Fato ou Fake (g1)"
FOF_SITE = "g1.globo.com"
# selo no começo do título: "É #FAKE que…", "É #FATO: …", "#NÃOÉBEMASSIM: …" (às vezes sem o #, como em
# "É FATO: …" ou "#É FAKE …", e às vezes depois do assunto, como em "Diogo Jota: É #FAKE que…"). Resumos
# com várias checagens ("Veja o que é #FATO ou #FAKE…") e notícias da seção sem selo não têm rótulo
# único e ficam de fora.
SELO_FOF = re.compile(r"^\s*(?:[^:#]{1,40}:\s+)?#?(?:É\s+)?#?(FATO|FAKE|N[ÃA]O\s*[ÉE]\s*BEM\s*ASSIM)(?![\w#])"
                      r"\s*:?\s*(?:que\s+)?")


def _titulo_fof(titulo):
    """Título do Fato ou Fake → (rótulo, frase checada), ou None se o título não começa com um selo."""
    titulo = (titulo or "").strip()
    m = SELO_FOF.match(titulo)
    if not m:
        return None
    selo = m.group(1) if m.group(1) in ("FATO", "FAKE") else "NÃOÉBEMASSIM"
    frase = titulo[m.end():].strip()
    return f"#{selo}", frase[:1].upper() + frase[1:]


def _fof(url, titulo, publicado, coletado_em, fonte):
    selo = _titulo_fof(titulo)
    if not selo:
        return []
    return [_registro(url, FOF_AGENCIA, FOF_SITE, selo[1], None, "pt", _data(None, url, publicacao=publicado),
                      selo[0], coletado_em, fonte, None)]


def _fato_ou_fake_rss(texto, coletado_em, busca=None):
    """RSS da seção (https://g1.globo.com/rss/g1/fato-ou-fake/): um registro por item com selo."""
    out = []
    for item in ElementTree.fromstring(texto).iter("item"):
        url = (item.findtext("link") or item.findtext("guid") or "").strip()
        quando = item.findtext("pubDate")
        dia = parsedate_to_datetime(quando).astimezone(BRASILIA).strftime("%Y-%m-%d") if quando else None
        out += _fof(url, html.unescape(item.findtext("title") or ""), dia, coletado_em, "fato_ou_fake_rss")
    return out


_CANONICA = re.compile(r"<link\b[^>]*\brel=\"canonical\"[^>]*\bhref=\"([^\"]+)\"")
_PUBLICADA = re.compile(r"<(?:meta|time)\b[^>]*\bitemprop=\"datePublished\"[^>]*\b(?:content|datetime)=\"([^\"]+)\"")
_MANCHETE = re.compile(r"<h1\b[^>]*\bcontent-head__title\b[^>]*>(.*?)</h1>", re.S)


def _metadados_pagina(texto):
    """Página de checagem do g1 → (URL canônica, título com o selo, dia da publicação em Brasília).
    O <title> e o og:title não têm o selo: vale a manchete (h1). A 1ª datePublished é a da matéria
    (as seguintes são de vídeos embutidos)."""
    url, quando, titulo = (_CANONICA.search(texto), _PUBLICADA.search(texto), _MANCHETE.search(texto))
    dia = None
    if quando:
        dia = datetime.fromisoformat(quando.group(1)).astimezone(BRASILIA).strftime("%Y-%m-%d")
    titulo = titulo and html.unescape(re.sub(r"<[^>]+>", "", titulo.group(1))).strip()
    return url and html.unescape(url.group(1)), titulo, dia


def _fato_ou_fake_pagina(texto, coletado_em, busca=None):
    """Página de uma checagem achada no sitemap diário (o sitemap só dá URL e data de modificação)."""
    url, titulo, dia = _metadados_pagina(texto)
    return _fof(url, titulo, dia, coletado_em, "fato_ou_fake_pagina") if url else []


FONTES = {"google_api": _google_api, "google_explorer": _google_explorer,
          "fato_ou_fake_rss": _fato_ou_fake_rss, "fato_ou_fake_pagina": _fato_ou_fake_pagina}


def recorte(fonte, bruto):
    """O que a coleta guarda de uma resposta do Fato ou Fake: só os metadados que o normalizador lê
    (URL, título com o selo e data), no formato da própria fonte. Nada do corpo da checagem."""
    esc = html.escape
    if fonte == "fato_ou_fake_pagina":
        url, titulo, dia = _metadados_pagina(bruto)
        return (f'<link rel="canonical" href="{esc(url or "")}">\n'
                f'<meta itemprop="datePublished" content="{esc(dia or "")}T12:00:00-03:00">\n'
                f'<h1 class="content-head__title" itemprop="headline">{esc(titulo or "")}</h1>\n')
    if fonte == "fato_ou_fake_rss":
        itens = [f"<item><title>{esc(item.findtext('title') or '')}</title>"
                 f"<link>{esc((item.findtext('link') or item.findtext('guid') or '').strip())}</link>"
                 f"<pubDate>{esc(item.findtext('pubDate') or '')}</pubDate></item>"
                 for item in ElementTree.fromstring(bruto).iter("item")]
        return "<?xml version='1.0' encoding='UTF-8'?>\n<rss version=\"2.0\"><channel>\n" + "\n".join(itens) + \
            "\n</channel></rss>\n"
    raise ValueError(f"sem recorte para a fonte {fonte!r}")


def normaliza(fonte, bruto, coletado_em, busca=None):
    """Resposta bruta de uma fonte → lista de registros de checagem (um por checagem)."""
    return FONTES[fonte](bruto, coletado_em, busca)


def junta(registros):
    """Um registro por id, na ordem em que apareceram. Vale o primeiro (coleta mais antiga); dos
    seguintes entram as buscas que o trouxeram e os campos que faltavam."""
    base = {}
    for r in registros:
        atual = base.get(r["id"])
        if atual is None:
            base[r["id"]] = {**r, "buscas": list(r["buscas"])}
            continue
        for campo, valor in r.items():
            if atual.get(campo) is None and valor is not None:
                atual[campo] = valor
        atual["buscas"] += [b for b in r["buscas"] if b not in atual["buscas"]]
    return list(base.values())
