"""Política de agências da base de checagens, lida de data/agencias.json (versionado):

- agências excluídas, com o motivo que a página mostra (casadas pelo nome como a fonte entrega ou
  pelo site da checagem);
- nome exibido de cada agência, para unificar grafias diferentes da mesma agência. Só o nome
  exibido (`agencia`) muda; o nome como veio fica em `agencia_na_fonte` e o rótulo nunca é tocado;
- como cada canal de coleta aparece na página ("via").

`separa` e `agencias_do_painel` são puras; a sessão de checagem usa `agencias_do_painel` para montar
`meta.agencias_incluidas` e `meta.agencias_excluidas` do arquivo de dados da página.
"""
import json
import unicodedata
from pathlib import Path

POLITICA = Path(__file__).resolve().parent.parent / "data" / "agencias.json"


def carrega(arquivo=POLITICA):
    return json.loads(Path(arquivo).read_text(encoding="utf-8"))


def _site_casa(site, dominios):
    site = (site or "").lower()
    return any(site == d or site.endswith("." + d) for d in dominios)


def _excluida(registro, politica):
    nome = registro.get("agencia_na_fonte") or registro.get("agencia")
    for e in politica.get("excluidas", []):
        if nome in e.get("nomes_na_fonte", []) or _site_casa(registro.get("site"), e.get("sites", [])):
            return e
    return None


def separa(registros, politica):
    """(entram, ficam de fora): os que entram vêm com `agencia` = nome exibido; nada mais muda."""
    nomes = politica.get("nomes_exibidos", {})
    dentro, fora = [], []
    for r in registros:
        na_fonte = r.get("agencia_na_fonte") or r.get("agencia")
        r = {**r, "agencia_na_fonte": na_fonte, "agencia": nomes.get(na_fonte, na_fonte)}
        (fora if _excluida(r, politica) else dentro).append(r)
    return dentro, fora


def _ordem(nome):
    return unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().casefold()


def agencias_do_painel(registros, politica=None):
    """Listas dos metadados da página: {"agencias_incluidas": [{nome, via}], "agencias_excluidas":
    [{nome, motivo}]}. Incluídas = agências presentes em `registros` (pelo nome exibido, em ordem
    alfabética), com os canais por onde chegaram; excluídas = todas as da política, na ordem dela,
    mesmo as que nunca apareceram na coleta (caso da Lupa)."""
    politica = politica if politica is not None else carrega()
    dentro, _ = separa(registros, politica)
    vias = politica.get("vias", {})
    canais = {}
    for r in dentro:
        lista = canais.setdefault(r["agencia"], [])
        via = vias.get(r.get("fonte_coleta"), r.get("fonte_coleta"))
        if via not in lista:
            lista.append(via)
    return {
        "agencias_incluidas": [{"nome": n, "via": " e ".join(canais[n])} for n in sorted(canais, key=_ordem)],
        "agencias_excluidas": [{"nome": e["nome"], "motivo": e["motivo"]} for e in politica.get("excluidas", [])],
    }
