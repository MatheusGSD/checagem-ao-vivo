"""Monta e consulta a base local de checagens (só metadados), coletada pela API oficial do Google
Fact Check (claims:search, languageCode=pt), com vetores multilingual-e5-small da frase checada.

Uso:
  python -P scripts/base_checagens.py coletar [--dias N] [--so N]   busca cada termo de
        data/buscas_checagens.json, pagina até esgotar, grava as respostas brutas e refaz a base
  python -P scripts/base_checagens.py reconstruir    refaz base e vetores a partir das respostas brutas
  python -P scripts/base_checagens.py relatorio      registros por agência e por mês, sem data de revisão
  python -P scripts/base_checagens.py consultar "frase" [...] [-n 8]   checagens mais parecidas

A chave vem de GOOGLE_FACTCHECK_KEY no ambiente ou no .env (ignorado pelo git). A base fica em
DADOS/checagens/ (fora do git), onde DADOS é --dados, a variável CHECAGEM_DADOS ou a pasta data/
do checkout principal (vale também rodando de um worktree):
  brutos/google_api/<início da coleta>.jsonl   uma linha por página: busca, página, parâmetros, resposta
  base.json                                    registros normalizados (ver checagens.py), um por URL
  vetores.npy + vetores.json                   e5-small float32 normalizado, na ordem de vetores.json["ids"]
  relatorio.txt
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checagens import junta, normaliza  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
API = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
POR_PAGINA = 100
PAUSA_S = 1.0
MODELO = "intfloat/multilingual-e5-small"


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


# ---------- base ----------

def le_brutos(pasta):
    """Todas as páginas brutas, da coleta mais antiga para a mais nova."""
    for arq in sorted((pasta / "brutos").glob("*/*.jsonl")):
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                yield json.loads(linha)


def reconstroi(pasta):
    """Base = todas as respostas brutas normalizadas e juntadas por URL."""
    registros = junta(r for pag in le_brutos(pasta)
                      for r in normaliza(pag["fonte"], pag["resposta"], pag["coletado_em"], pag["busca"]))
    (pasta / "base.json").write_text(json.dumps({"atualizado_em": agora(), "registros": registros},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    return registros


def le_base(pasta):
    return json.loads((pasta / "base.json").read_text(encoding="utf-8"))["registros"]


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
    if termos_afirmacoes:
        achadas = Counter(b for r in registros for b in r["buscas"])
        com = [t for t in termos_afirmacoes if achadas[t]]
        linhas += ["", f"Afirmações da Band com algum resultado na busca: {len(com)} de {len(termos_afirmacoes)}"]
    return "\n".join(linhas) + "\n"


def termos_afirmacoes(arquivo):
    return [a["busca"] for a in json.loads(Path(arquivo).read_text(encoding="utf-8"))["afirmacoes_band"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("comando", choices=["coletar", "reconstruir", "relatorio", "consultar"])
    ap.add_argument("frases", nargs="*")
    ap.add_argument("--dados", help="pasta de dados (padrão: CHECAGEM_DADOS ou data/ do checkout principal)")
    ap.add_argument("--buscas", default=RAIZ / "data" / "buscas_checagens.json")
    ap.add_argument("--dias", type=int, help="só checagens dos últimos N dias (maxAgeDays)")
    ap.add_argument("--so", type=int, help="só os N primeiros termos (teste)")
    ap.add_argument("-n", type=int, default=8)
    a = ap.parse_args()
    pasta = pasta_base(a.dados)

    if a.comando == "consultar":
        for frase in a.frases:
            print(f"\n{frase!r}")
            for s, r in consulta(pasta, frase, a.n):
                print(f"  {s:.3f}  {r['data'] or '?':10}  {r['agencia'][:18]:18}  {r['rotulo'][:16] if r['rotulo'] else '?':16}"
                      f"  {(r['frase'] or '')[:90]}\n{'':34}{r['url']}")
        return

    if a.comando == "coletar":
        termos = termos_de_busca(a.buscas)[: a.so]
        inicio = agora()
        saida = pasta / "brutos" / "google_api" / f"{inicio.replace(':', '-')}.jsonl"
        print(f"coleta: {len(termos)} buscas → {saida}")
        paginas, erros = coleta_google(termos, chave_google(), saida, dias=a.dias)
        print(f"coleta: {paginas} páginas, {erros} buscas com erro")

    if a.comando in ("coletar", "reconstruir"):
        registros = reconstroi(pasta)
        print(f"base: {len(registros)} registros; {atualiza_indice(pasta, registros)} vetores novos")

    texto = relatorio(le_base(pasta), termos_afirmacoes(a.buscas))
    (pasta / "relatorio.txt").write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
