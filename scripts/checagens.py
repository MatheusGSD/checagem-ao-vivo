"""Base local de checagens: normalizador de cada fonte num registro uniforme (só metadados).

Um registro de checagem tem: id (URL canônica), url, agência (nome exibido e nome como a fonte
entregou) e site, frase checada, quem disse,
idioma, data e de onde ela veio (revisao, publicacao ou afirmacao), rótulo como a fonte entrega,
data e fonte da coleta e as buscas que o trouxeram. Nada do corpo do texto da checagem.
`normaliza` e `junta` são puras (sem rede); a coleta e o índice ficam em base_checagens.py.

Fontes:
  google_api       resposta JSON de claims:search da API oficial do Google Fact Check
  google_explorer  resposta do buscador interno do Fact Check Explorer (reserva, desligado por padrão)
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


FONTES = {"google_api": _google_api, "google_explorer": _google_explorer}


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
