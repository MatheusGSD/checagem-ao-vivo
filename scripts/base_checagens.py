"""Monta e consulta a base local de checagens (só metadados), coletada pela API oficial do Google
Fact Check (claims:search, languageCode=pt), com vetores multilingual-e5-small da frase checada.

Uso:
  python -P scripts/base_checagens.py coletar [--dias N] [--so N] [--so-novas]   busca cada termo de
        data/buscas_checagens.json (e as variantes curtas), pagina até esgotar, grava as respostas
        brutas e refaz a base, sem as agências excluídas em data/agencias.json
  python -P scripts/base_checagens.py reconstruir    refaz base e vetores a partir das respostas brutas
  python -P scripts/base_checagens.py relatorio      registros por agência e por mês, sem data de revisão
  python -P scripts/base_checagens.py consultar "frase" [...] [-n 8]   checagens mais parecidas
  python -P scripts/base_checagens.py fato-ou-fake [--desde 2025-01-01]   RSS da seção e sitemaps diários
        do g1 filtrados pela seção (robots.txt conferido, pausa entre requisições, só o começo de cada
        página); guarda só o recorte (URL, título com o selo, data) e acrescenta à base o que for novo
  python -P scripts/base_checagens.py atualizar [--dias 1]   durante o debate (a cada 10 min): Google com
        maxAgeDays e RSS do Fato ou Fake; só os registros novos entram na base e ganham vetores
  python -P scripts/base_checagens.py incorporar BRUTO.jsonl [...]   acrescenta à base o que for novo

A chave vem de GOOGLE_FACTCHECK_KEY no ambiente ou no .env (ignorado pelo git). A base fica em
DADOS/checagens/ (fora do git), onde DADOS é --dados, a variável CHECAGEM_DADOS ou a pasta data/
do checkout principal (vale também rodando de um worktree):
  brutos/google_api/<início da coleta>.jsonl   uma linha por página: busca, página, parâmetros, resposta
  brutos/fato_ou_fake/<início>.jsonl           uma linha por leitura do RSS ou página: recorte (só metadados)
  brutos/fato_ou_fake/sitemaps.json            URLs da seção em cada sitemap diário já lido
  base.json                                   registros normalizados (ver checagens.py), um por URL
  vetores.npy + vetores.json                   e5-small float32 normalizado, na ordem de vetores.json["ids"]
  relatorio.txt
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agencias import carrega as politica_de_agencias, separa  # noqa: E402
from checagens import junta, normaliza, recorte, url_canonica  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
API = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
POR_PAGINA = 100
PAUSA_S = 1.0
MODELO = "intfloat/multilingual-e5-small"
AGENTE = "checagem-ao-vivo/1.0 (painel Lula x Flavio; so metadados das checagens)"
G1_RAIZ = "https://g1.globo.com"
FOF_RSS = f"{G1_RAIZ}/rss/g1/fato-ou-fake/"
G1_SITEMAP = f"{G1_RAIZ}/sitemap/g1/sitemap.xml"
SECAO_FOF = "/fato-ou-fake/"
SITEMAP_DIA = re.compile(r"/sitemap/g1/(\d{4})/(\d\d)/(\d\d)_\d+\.xml$")
FOF_DESDE = "2025-01-01"


def raiz_principal():
    """Raiz do checkout principal (num worktree, data/ e .env só existem lá)."""
    try:
        comum = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=RAIZ,
                               capture_output=True, text=True, check=True).stdout.strip()
        return Path(comum).parent
    except (OSError, subprocess.CalledProcessError):
        return RAIZ


def pasta_base(dados=None):
    dados = dados or os.environ.get("CHECAGEM_DADOS") or raiz_principal() / "data"
    return Path(dados) / "checagens"


def chave_google():
    if os.environ.get("GOOGLE_FACTCHECK_KEY"):
        return os.environ["GOOGLE_FACTCHECK_KEY"]
    for env in [RAIZ / ".env", raiz_principal() / ".env"]:
        if env.exists():
            for linha in env.read_text(encoding="utf-8").splitlines():
                nome, _, valor = linha.partition("=")
                if nome.strip() == "GOOGLE_FACTCHECK_KEY" and valor.strip():
                    return valor.strip()
    sys.exit("falta GOOGLE_FACTCHECK_KEY no ambiente ou no .env")


def agora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def termos_de_busca(arquivo):
    """Lista de buscas do arquivo versionado, sem repetição, na ordem do arquivo."""
    d = json.loads(Path(arquivo).read_text(encoding="utf-8"))
    termos = d["nomes"] + d["temas_telao"] + d["regioes"] + [t for lst in d["assuntos"].values() for t in lst]
    termos += [a["busca"] for a in d["afirmacoes_band"]]
    termos += [v for vs in d.get("variantes_curtas", {}).values() for v in vs]  # depois das originais
    return list(dict.fromkeys(termos))


# ---------- coleta ----------

def coleta_google(termos, chave, saida, dias=None, pausa=PAUSA_S, log=print):
    """Busca cada termo e pagina até não vir nextPageToken. Grava cada página como uma linha de
    `saida` (JSONL, sem a chave) assim que chega, para não perder nada se a coleta cair."""
    import requests

    sessao = requests.Session()
    saida.parent.mkdir(parents=True, exist_ok=True)
    paginas = erros = 0
    with saida.open("a", encoding="utf-8") as f:
        for i, termo in enumerate(termos, 1):
            params = {"query": termo, "languageCode": "pt", "pageSize": POR_PAGINA}
            if dias:
                params["maxAgeDays"] = dias
            pagina, token, n = 0, None, 0
            while True:
                p = {**params, **({"pageToken": token} if token else {})}
                resposta = None
                for tentativa in range(4):
                    time.sleep(pausa * (1 if tentativa == 0 else 5 * tentativa))
                    try:
                        r = sessao.get(API, params={**p, "key": chave}, timeout=30)
                    except requests.RequestException as e:
                        log(f"  rede: {type(e).__name__} ({termo!r}, página {pagina})")
                        continue
                    if r.status_code == 200:
                        resposta = r.json()
                        break
                    log(f"  HTTP {r.status_code} ({termo!r}, página {pagina})")
                    if r.status_code not in (429, 500, 502, 503, 504):
                        break
                if resposta is None:
                    erros += 1
                    break
                f.write(json.dumps({"fonte": "google_api", "busca": termo, "pagina": pagina, "parametros": p,
                                    "coletado_em": agora(), "resposta": resposta}, ensure_ascii=False) + "\n")
                f.flush()
                paginas += 1
                n += len(resposta.get("claims", []))
                token = resposta.get("nextPageToken")
                if not token:
                    break
                pagina += 1
            log(f"[{i}/{len(termos)}] {n:4d} afirmações em {pagina + 1} página(s)  {termo[:70]}")
    return paginas, erros


# ---------- coleta do Fato ou Fake (g1): só o RSS da seção e o sitemap diário filtrado pela seção ----------

class G1:
    """Cliente educado do g1: confere o robots.txt antes de cada URL, se identifica e espera
    `pausa` segundos entre requisições."""

    def __init__(self, pausa=PAUSA_S, log=print):
        import requests
        from urllib.robotparser import RobotFileParser

        self.pausa, self.log, self._ultima = pausa, log, 0.0
        self.http = requests.Session()
        self.http.headers["User-Agent"] = AGENTE
        self.robots = RobotFileParser()
        self.robots.parse(self._pede(f"{G1_RAIZ}/robots.txt").text.splitlines())

    def _pede(self, url, **kw):
        espera = self._ultima + self.pausa - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        try:
            return self.http.get(url, timeout=30, **kw)
        finally:
            self._ultima = time.monotonic()

    def get(self, url, **kw):
        """Resposta 200 ou None (bloqueado pelo robots.txt, erro HTTP ou de rede, registrados no log)."""
        if not self.robots.can_fetch(AGENTE, url):
            self.log(f"  robots.txt bloqueia {url}")
            return None
        for tentativa in range(3):
            try:
                r = self._pede(url, **kw)
            except Exception as e:  # rede
                self.log(f"  rede: {type(e).__name__} ({url})")
                time.sleep(5 * (tentativa + 1))
                continue
            if r.status_code == 200:
                return r
            self.log(f"  HTTP {r.status_code} ({url})")
            if r.status_code not in (429, 500, 502, 503, 504):
                return None
            time.sleep(10 * (tentativa + 1))
        return None

    def cabeca(self, url, limite=600_000):
        """Começo da página até a manchete (onde ficam URL canônica, data e título com o selo): o
        resto, com o corpo da checagem, nem é baixado."""
        r = self.get(url, stream=True)
        if r is None:
            return None
        lido = b""
        with r:
            for pedaco in r.iter_content(32_768):
                lido += pedaco
                if b"content-head__title" in lido and b"</h1>" in lido.split(b"content-head__title", 1)[1]:
                    break
                if len(lido) > limite:
                    break
        return lido.decode("utf-8", errors="replace")


def _linha(fonte, resposta, **extra):
    return json.dumps({"fonte": fonte, "busca": None, "coletado_em": agora(), **extra, "resposta": resposta},
                      ensure_ascii=False) + "\n"


def coleta_fof_rss(g1, saida):
    """Uma leitura do RSS da seção, guardada só como recorte (título, link e data de cada item)."""
    r = g1.get(FOF_RSS)
    if r is None:
        return 0
    r.encoding = "utf-8"
    saida.parent.mkdir(parents=True, exist_ok=True)
    with saida.open("a", encoding="utf-8") as f:
        f.write(_linha("fato_ou_fake_rss", recorte("fato_ou_fake_rss", r.text), url=FOF_RSS))
    return 1


def _urls_fof_conhecidas(pasta):
    """URLs do Fato ou Fake já guardadas nas coletas anteriores (páginas e itens do RSS)."""
    vistas = set()
    for pag in le_brutos(pasta):
        if pag["fonte"] == "fato_ou_fake_pagina":
            vistas.add(url_canonica(pag["url"]))
        elif pag["fonte"] == "fato_ou_fake_rss":
            vistas |= {r["id"] for r in normaliza(pag["fonte"], pag["resposta"], pag["coletado_em"])}
    return vistas


def coleta_fof_sitemap(g1, pasta, saida, desde=FOF_DESDE, log=print):
    """Sitemaps diários do g1 desde `desde`, filtrados pela seção; de cada checagem nova, o começo da
    página, guardado só como recorte. A lista de URLs de cada sitemap diário fica em
    brutos/fato_ou_fake/sitemaps.json, para não baixar de novo os dias que não mudaram."""
    import xml.etree.ElementTree as ET

    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    cache_arq = pasta / "brutos" / "fato_ou_fake" / "sitemaps.json"
    cache = json.loads(cache_arq.read_text(encoding="utf-8")) if cache_arq.exists() else {}
    r = g1.get(G1_SITEMAP)
    if r is None:
        sys.exit("não consegui ler o índice de sitemaps do g1")
    diarios = []
    for sm in ET.fromstring(r.content).findall("s:sitemap", ns):
        loc, lastmod = sm.findtext("s:loc", namespaces=ns), sm.findtext("s:lastmod", namespaces=ns) or ""
        m = SITEMAP_DIA.search(loc or "")
        if m and "-".join(m.groups()) >= desde:
            diarios.append((loc, lastmod))
    diarios.sort()
    log(f"sitemaps diários desde {desde}: {len(diarios)}")
    cache_arq.parent.mkdir(parents=True, exist_ok=True)
    for k, (loc, lastmod) in enumerate(diarios, 1):
        visto = cache.get(loc)
        if visto and visto["lastmod"] == lastmod and lastmod < visto["buscado_em"][:10]:
            continue  # o dia não mudou desde que foi lido (e não era o dia corrente)
        r = g1.get(loc)
        if r is None:
            continue
        urls = [u.findtext("s:loc", namespaces=ns) for u in ET.fromstring(r.content).findall("s:url", ns)]
        cache[loc] = {"lastmod": lastmod, "buscado_em": agora(), "urls": [u for u in urls if u and SECAO_FOF in u]}
        if k % 20 == 0 or k == len(diarios):
            cache_arq.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
            log(f"  [{k}/{len(diarios)}] {loc}")
    cache_arq.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")

    conhecidas = _urls_fof_conhecidas(pasta)
    novas = list(dict.fromkeys(u for loc, _ in diarios for u in cache.get(loc, {}).get("urls", [])
                               if url_canonica(u) not in conhecidas))
    log(f"checagens do Fato ou Fake nos sitemaps: {sum(len(c['urls']) for c in cache.values())}; novas: {len(novas)}")
    paginas = 0
    with saida.open("a", encoding="utf-8") as f:
        for k, url in enumerate(novas, 1):
            texto = g1.cabeca(url)
            if texto is None:
                continue
            f.write(_linha("fato_ou_fake_pagina", recorte("fato_ou_fake_pagina", texto), url=url))
            f.flush()
            paginas += 1
            if k % 50 == 0:
                log(f"  [{k}/{len(novas)}] páginas")
    return paginas


# ---------- base ----------

def le_brutos(pasta):
    """Todas as páginas brutas, da coleta mais antiga para a mais nova."""
    for arq in sorted((pasta / "brutos").glob("*/*.jsonl")):
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                yield json.loads(linha)


def reconstroi(pasta):
    """Base = todas as respostas brutas normalizadas e juntadas por URL, menos as agências excluídas
    em data/agencias.json (contadas em "fora_da_base") e com o nome exibido de cada agência."""
    registros, fora = separa(junta(r for pag in le_brutos(pasta)
                                   for r in normaliza(pag["fonte"], pag["resposta"], pag["coletado_em"], pag["busca"])),
                             politica_de_agencias())
    (pasta / "base.json").write_text(json.dumps({"atualizado_em": agora(), "registros": registros,
                                                 "fora_da_base": Counter(r["agencia"] for r in fora)},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    return registros


def le_base(pasta):
    return json.loads((pasta / "base.json").read_text(encoding="utf-8"))["registros"]


def le_jsonl(arquivos):
    for arq in arquivos:
        if Path(arq).exists():
            yield from (json.loads(linha) for linha in Path(arq).read_text(encoding="utf-8").splitlines() if linha.strip())


def incorpora(pasta, paginas):
    """Atualização incremental: acrescenta ao fim da base os registros das páginas brutas dadas cujo
    id a base ainda não tem, sem mexer nos que já estão lá, e calcula vetores só para eles.
    Devolve os registros novos (para Sessao.atualiza_base)."""
    arq = pasta / "base.json"
    base = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {"registros": []}
    ja = {r["id"] for r in base["registros"]}
    novos, fora = separa([r for r in junta(r for pag in paginas for r in normaliza(
        pag["fonte"], pag["resposta"], pag["coletado_em"], pag["busca"])) if r["id"] not in ja], politica_de_agencias())
    if novos or not arq.exists():
        fora_da_base = Counter(base.get("fora_da_base") or {}) + Counter(r["agencia"] for r in fora)
        base = {**base, "atualizado_em": agora(), "registros": base["registros"] + novos, "fora_da_base": fora_da_base}
        tmp = arq.with_suffix(".tmp")
        tmp.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, arq)
        atualiza_indice(pasta, base["registros"])
    return novos


def atualiza(pasta, termos, chave, dias=1, pausa=PAUSA_S, pausa_google=0.5, log=print, ao_erro=None):
    """Atualização durante o debate: Google só com checagens dos últimos `dias` dias e o RSS do Fato
    ou Fake; o que vier de novo entra na base (ver incorpora). Devolve os registros novos.
    ao_erro(mensagem): buscas do Google que falharam e RSS fora do ar (a tela de aprovação)."""
    inicio = agora().replace(":", "-")
    saidas = [pasta / "brutos" / "google_api" / f"{inicio}.jsonl", pasta / "brutos" / "fato_ou_fake" / f"{inicio}.jsonl"]
    falhas = []
    _, erros = coleta_google(termos, chave, saidas[0], dias=dias, pausa=pausa_google,
                             log=lambda m: falhas.append(m.strip()) if m.lstrip().startswith(("HTTP", "rede")) else None)
    if erros and ao_erro:
        ao_erro(f"API do Google: {erros} de {len(termos)} buscas falharam (último: {falhas[-1] if falhas else '?'})")
    try:  # o g1 fora do ar não pode levar junto o que veio do Google
        if not coleta_fof_rss(G1(pausa=pausa, log=log), saidas[1]) and ao_erro:
            ao_erro("RSS do Fato ou Fake não respondeu")
    except Exception as e:  # noqa: BLE001
        if not ao_erro:
            raise
        ao_erro(f"RSS do Fato ou Fake: {type(e).__name__}: {e}")
    novos = incorpora(pasta, le_jsonl(saidas))
    log(f"atualização {inicio}: {len(novos)} checagens novas "
        + ", ".join(f"{ag} {n}" for ag, n in Counter(r["agencia"] for r in novos).most_common()))
    return novos


# ---------- índice (vetores) ----------

_modelo = None


def modelo():
    global _modelo
    if _modelo is None:
        from sentence_transformers import SentenceTransformer
        _modelo = SentenceTransformer(MODELO)
    return _modelo


def atualiza_indice(pasta, registros):
    """Vetores 'passage: frase' na ordem da base; só codifica o que é novo ou mudou de frase."""
    import numpy as np

    antigos = {}
    if (pasta / "vetores.json").exists():
        meta = json.loads((pasta / "vetores.json").read_text(encoding="utf-8"))
        X = np.load(pasta / "vetores.npy")
        antigos = {(i, f): X[k] for k, (i, f) in enumerate(zip(meta["ids"], meta["frases"]))}
    chaves = [(r["id"], r["frase"] or "") for r in registros]
    novos = [c for c in chaves if c not in antigos]
    if novos:
        V = modelo().encode([f"passage: {f}" for _, f in novos], normalize_embeddings=True,
                            batch_size=32, show_progress_bar=False)
        antigos.update(zip(novos, V))
    X = np.stack([antigos[c] for c in chaves]).astype(np.float32) if chaves else np.zeros((0, 384), np.float32)
    np.save(pasta / "vetores.npy", X)
    (pasta / "vetores.json").write_text(json.dumps({"modelo": MODELO, "dim": int(X.shape[1]),
                                                    "ids": [i for i, _ in chaves], "frases": [f for _, f in chaves]},
                                                   ensure_ascii=False), encoding="utf-8")
    return len(novos)


class Indice:
    """Base de checagens em memória com os vetores da frase checada, para a sessão consultar a
    cada afirmação sem reler os arquivos. Pode ser usado por várias threads."""

    def __init__(self, registros, vetores=None):
        import numpy as np

        self.registros = list(registros)
        if not self.registros:
            vetores = np.zeros((0, 384), np.float32)
        elif vetores is None:
            vetores = modelo().encode([f"passage: {r['frase'] or ''}" for r in self.registros],
                                      normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        self.X = np.asarray(vetores, dtype=np.float32).reshape(len(self.registros), -1)
        self._trava = threading.Lock()

    @classmethod
    def da_pasta(cls, pasta):
        """A base e os vetores gravados em `pasta` (ver atualiza_indice)."""
        import numpy as np

        registros = le_base(pasta)
        meta = json.loads((pasta / "vetores.json").read_text(encoding="utf-8"))
        if meta["ids"] != [r["id"] for r in registros]:
            sys.exit(f"vetores fora de ordem com a base em {pasta}: rode `base_checagens.py reconstruir`")
        return cls(registros, np.load(pasta / "vetores.npy"))

    def acrescenta(self, registros):
        """Põe na base os registros com id que ela ainda não tem (atualização durante o debate) e
        devolve esses registros novos. Só eles ganham vetores."""
        import numpy as np

        with self._trava:
            ja = {r["id"] for r in self.registros}
            novos = list({r["id"]: r for r in registros if r["id"] not in ja}.values())
            if novos:
                V = modelo().encode([f"passage: {r['frase'] or ''}" for r in novos], normalize_embeddings=True,
                                    batch_size=32, show_progress_bar=False)
                # troca de uma vez: uma busca em curso segue com a lista e os vetores que pegou
                self.registros, self.X = self.registros + novos, np.vstack([self.X, np.asarray(V, np.float32)])
        return novos

    def busca(self, frase, n=8, desde=None, entre=None):
        """As n checagens com frase mais parecida: [(cosseno, registro)], da mais parecida para a
        menos. Com `desde` ("AAAA-MM-DD"), só entram checagens com data a partir desse dia; com
        `entre` (ids), só essas checagens."""
        import numpy as np

        with self._trava:
            registros, X = self.registros, self.X
            if not registros:
                return []
            q = modelo().encode([f"query: {frase}"], normalize_embeddings=True, show_progress_bar=False)[0]
        s = X @ q
        out = []
        for k in np.argsort(-s):
            r = registros[k]
            if desde and not (r["data"] and r["data"] >= desde):
                continue
            if entre is not None and r["id"] not in entre:
                continue
            out.append((float(s[k]), r))
            if len(out) == n:
                break
        return out


def consulta(pasta, frase, n=8):
    """As n checagens com frase mais parecida: [(cosseno, registro)], da mais parecida para a menos."""
    import numpy as np

    meta = json.loads((pasta / "vetores.json").read_text(encoding="utf-8"))
    X = np.load(pasta / "vetores.npy")
    por_id = {r["id"]: r for r in le_base(pasta)}
    q = modelo().encode([f"query: {frase}"], normalize_embeddings=True)[0]
    s = X @ q
    return [(float(s[k]), por_id[meta["ids"][k]]) for k in np.argsort(-s)[:n]]


# ---------- relatório ----------

def relatorio(registros, termos_afirmacoes=()):
    linhas = [f"Base de checagens: {len(registros)} registros (um por URL)", ""]
    por_agencia = Counter(r["agencia"] for r in registros)
    linhas.append("Por agência:")
    linhas += [f"  {n:5d}  {ag}" for ag, n in por_agencia.most_common()]
    meses = Counter((r["data"] or "sem data")[:7] for r in registros)
    linhas += ["", "Por mês (data da checagem):"]
    linhas += [f"  {m}  {meses[m]:5d}" for m in sorted(meses, reverse=True)]
    origem = Counter(r["data_origem"] or "nenhuma" for r in registros)
    sem_rev = len(registros) - origem["revisao"]
    linhas += ["", f"Sem data de revisão: {sem_rev} de {len(registros)} "
                   f"(data de publicação: {origem['publicacao']}; da afirmação: {origem['afirmacao']}; "
                   f"nenhuma data: {origem['nenhuma']})"]
    linhas.append("  por agência: " + ", ".join(f"{ag} {n}" for ag, n in Counter(
        r["agencia"] for r in registros if r["data_origem"] != "revisao").most_common()))
    if termos_afirmacoes:  # cada afirmação: a busca original ou [original, variantes curtas...]
        achadas = Counter(b for r in registros for b in r["buscas"])
        grupos = [[t] if isinstance(t, str) else t for t in termos_afirmacoes]
        com = [g for g in grupos if any(achadas[t] for t in g)]
        linhas += ["", f"Afirmações da Band com algum resultado na busca: {len(com)} de {len(grupos)}"]
    return "\n".join(linhas) + "\n"


def afirmacoes_com_variantes(arquivo):
    """Uma lista por afirmação da Band: a busca original e as variantes curtas dela."""
    d = json.loads(Path(arquivo).read_text(encoding="utf-8"))
    variantes = d.get("variantes_curtas", {})
    return [[a["busca"], *variantes.get(a["busca"], [])] for a in d["afirmacoes_band"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("comando", choices=["coletar", "reconstruir", "relatorio", "consultar",
                                        "fato-ou-fake", "atualizar", "incorporar"])
    ap.add_argument("frases", nargs="*")
    ap.add_argument("--dados", help="pasta de dados (padrão: CHECAGEM_DADOS ou data/ do checkout principal)")
    ap.add_argument("--buscas", default=RAIZ / "data" / "buscas_checagens.json")
    ap.add_argument("--dias", type=int, help="só checagens dos últimos N dias (maxAgeDays)")
    ap.add_argument("--so", type=int, help="só os N primeiros termos (teste)")
    ap.add_argument("--so-novas", action="store_true", help="só os termos que nunca foram buscados (sem resposta bruta)")
    ap.add_argument("-n", type=int, default=8)
    ap.add_argument("--desde", default=FOF_DESDE, help="fato-ou-fake: sitemaps diários a partir deste dia")
    ap.add_argument("--pausa", type=float, help=f"segundos entre requisições (padrão: {PAUSA_S} no g1, 0.5 no Google ao atualizar)")
    a = ap.parse_args()
    pasta = pasta_base(a.dados)

    if a.comando == "consultar":
        for frase in a.frases:
            print(f"\n{frase!r}")
            for s, r in consulta(pasta, frase, a.n):
                print(f"  {s:.3f}  {r['data'] or '?':10}  {r['agencia'][:18]:18}  {r['rotulo'][:16] if r['rotulo'] else '?':16}"
                      f"  {(r['frase'] or '')[:90]}\n{'':34}{r['url']}")
        return

    if a.comando == "atualizar":  # durante o debate, a cada 10 min
        atualiza(pasta, termos_de_busca(a.buscas), chave_google(), dias=a.dias or 1,
                 **({"pausa": a.pausa, "pausa_google": a.pausa} if a.pausa is not None else {}))
        return
    if a.comando == "incorporar":  # arquivos brutos (.jsonl) coletados à parte
        novos = incorpora(pasta, le_jsonl(a.frases))
        print(f"base: {len(novos)} registros novos " + ", ".join(
            f"{ag} {n}" for ag, n in Counter(r["agencia"] for r in novos).most_common()))
        return
    if a.comando == "fato-ou-fake":
        g1 = G1(pausa=a.pausa if a.pausa is not None else PAUSA_S)
        saida = pasta / "brutos" / "fato_ou_fake" / f"{agora().replace(':', '-')}.jsonl"
        coleta_fof_rss(g1, saida)
        print(f"coleta do Fato ou Fake desde {a.desde} → {saida}")
        print(f"páginas lidas: {coleta_fof_sitemap(g1, pasta, saida, desde=a.desde)}")
        novos = incorpora(pasta, le_jsonl([saida]))
        print(f"base: {len(novos)} registros novos do Fato ou Fake: "
              + ", ".join(f"{s} {n}" for s, n in Counter(r["rotulo"] for r in novos).most_common()))

    if a.comando == "coletar":
        termos = termos_de_busca(a.buscas)[: a.so]
        if a.so_novas:
            ja = {pag["busca"] for pag in le_brutos(pasta)}
            termos = [t for t in termos if t not in ja]
        inicio = agora()
        saida = pasta / "brutos" / "google_api" / f"{inicio.replace(':', '-')}.jsonl"
        print(f"coleta: {len(termos)} buscas → {saida}")
        paginas, erros = coleta_google(termos, chave_google(), saida, dias=a.dias)
        print(f"coleta: {paginas} páginas, {erros} buscas com erro")

    if a.comando in ("coletar", "reconstruir"):
        registros = reconstroi(pasta)
        print(f"base: {len(registros)} registros; {atualiza_indice(pasta, registros)} vetores novos")

    texto = relatorio(le_base(pasta), afirmacoes_com_variantes(a.buscas))
    fora = json.loads((pasta / "base.json").read_text(encoding="utf-8")).get("fora_da_base") or {}
    if fora:
        texto += "\nFora da base (data/agencias.json): " + ", ".join(f"{ag} {n}" for ag, n in fora.items()) + "\n"
    (pasta / "relatorio.txt").write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
