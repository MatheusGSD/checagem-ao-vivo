"""Cobertura das 73 afirmações da Band pela base de checagens, medida por proximidade semântica e não
pela contagem de resultados da busca por palavra-chave na API do Google.

Para cada afirmação (a reformulação neutra de data/buscas_checagens.json), as 3 checagens da base
com frase mais parecida (e5-small, 'query:' contra 'passage:', como em base_checagens.consulta), com
a similaridade. Só lista candidatas para o operador conferir de olho: similaridade sozinha não diz
que é a mesma afirmação.

Uso: python -P scripts/cobertura_band.py [--dados PASTA] [-n 3]
Grava DADOS/checagens/cobertura_band.md (fora do git).
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_checagens import RAIZ, afirmacoes_com_variantes, agora, le_base, modelo, pasta_base  # noqa: E402

PISO = (0.88, 0.90)  # faixa de ruído achada no ticket 01: frases sem relação já chegam a 0,88–0,90


def faixa(s):
    return f"≥ {PISO[1]:.2f}" if s >= PISO[1] else f"{PISO[0]:.2f}–{PISO[1]:.2f}" if s >= PISO[0] else f"< {PISO[0]:.2f}"


def cel(texto):
    return (texto or "").replace("|", "\\|").replace("\n", " ")


def main():
    import numpy as np

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dados")
    ap.add_argument("--buscas", default=RAIZ / "data" / "buscas_checagens.json")
    ap.add_argument("-n", type=int, default=3)
    a = ap.parse_args()
    pasta = pasta_base(a.dados)

    afirmacoes = json.loads(Path(a.buscas).read_text(encoding="utf-8"))["afirmacoes_band"]
    grupos = afirmacoes_com_variantes(a.buscas)
    meta = json.loads((pasta / "vetores.json").read_text(encoding="utf-8"))
    X = np.load(pasta / "vetores.npy")
    base = le_base(pasta)
    por_id = {r["id"]: r for r in base}
    achadas = Counter(b for r in base for b in r["buscas"])

    Q = modelo().encode([f"query: {af['busca']}" for af in afirmacoes], normalize_embeddings=True,
                        batch_size=32, show_progress_bar=False)
    S = Q @ X.T
    linhas, topo, api = [], [], Counter()
    for af, grupo, s in zip(afirmacoes, grupos, S):
        idx = np.argsort(-s)[: a.n]
        topo.append(float(s[idx[0]]))
        original, variantes = achadas[grupo[0]], sum(achadas[v] for v in grupo[1:])
        situacao = ("busca original com resultado" if original else
                    "só as variantes curtas com resultado" if variantes else "nenhum resultado na API")
        api[situacao] += 1
        linhas += [f"### {int(af['t']) // 60:02d}:{int(af['t']) % 60:02d} · {af['quem']}", "",
                   f"> {af['busca']}", "",
                   f"Busca por palavra-chave: {situacao}"
                   + (f" (variantes: {', '.join(grupo[1:])})" if len(grupo) > 1 else "") + ".", "",
                   "| sim. | agência | data | rótulo | frase checada |", "|---|---|---|---|---|"]
        for k in idx:
            r = por_id[meta["ids"][k]]
            linhas.append(f"| {s[k]:.3f} | {cel(r['agencia'])} | {r['data'] or '?'} | {cel(r['rotulo'])} "
                          f"| [{cel(r['frase'])[:160]}]({r['url']}) |")
        linhas.append("")

    faixas = Counter(faixa(t) for t in topo)
    cab = [
        "# Cobertura das afirmações da Band por proximidade semântica", "",
        f"Gerado em {agora()} · base com {len(base)} checagens · {len(afirmacoes)} afirmações · "
        f"modelo {meta['modelo']} (`query:` × `passage:`)", "",
        "**Isto lista candidatas, não ligações.** A similaridade sozinha tem um piso de ruído por volta de "
        f"{PISO[0]:.2f}–{PISO[1]:.2f} (achado do ticket 01: frases sem relação com a afirmação já chegam a esses "
        "valores), então uma nota alta não quer dizer que a checagem trata da mesma afirmação. Quem decide é a "
        "leitura: mesmo sujeito, mesmo número, mesmo período.", "",
        "## Resumo", "",
        "Melhor candidata de cada afirmação, por faixa de similaridade:", "",
        "| faixa | afirmações |", "|---|---|",
        *[f"| {f} | {faixas[f]} |" for f in (f"≥ {PISO[1]:.2f}", f"{PISO[0]:.2f}–{PISO[1]:.2f}", f"< {PISO[0]:.2f}")],
        "", f"Mediana da melhor similaridade: {float(np.median(topo)):.3f}", "",
        "Busca por palavra-chave na API do Google (com a política de agências aplicada):", "",
        "| situação | afirmações |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in api.most_common()], "",
        "## Por afirmação (ordem do debate)", "",
    ]
    saida = pasta / "cobertura_band.md"
    saida.write_text("\n".join(cab + linhas), encoding="utf-8")
    print(f"{saida}\n" + "\n".join(cab[8:-3]))


if __name__ == "__main__":
    main()
