"""Prepara a busca por sentido da transcrição (opcional no painel; o leitor ativa e baixa o modelo).

Uso: python busca.py SITE_DIR [consulta ...]
Lê SITE_DIR/data/dados.json, corta cada fala dos candidatos em trechos de até ~40 palavras (no fim das
frases), calcula o embedding de cada trecho com intfloat/multilingual-e5-small e grava
SITE_DIR/data/busca.json com os vetores em int8 (base64). No navegador, o mesmo modelo (porte ONNX
Xenova/multilingual-e5-small, quantizado) transforma a consulta em vetor e compara com estes.
Com consultas na linha de comando, mostra as 3 falas mais próximas de cada uma (para conferir).
"""
import base64
import json
import re
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

MODELO, MODELO_NAVEGADOR = "intfloat/multilingual-e5-small", "Xenova/multilingual-e5-small"
MAX_PALAVRAS, MIN_PALAVRAS = 40, 4

site = Path(sys.argv[1])
d = json.loads((site / "data" / "dados.json").read_text(encoding="utf-8"))


def trechos(texto):
    """Posições [a, b) de trechos de até MAX_PALAVRAS palavras, cortados no fim das frases."""
    frases = [(m.start(), m.end()) for m in re.finditer(r"[^.?!…]+[.?!…]*", texto) if m.group().strip()]
    out, ini, n = [], None, 0
    for a, b in frases:
        k = len(texto[a:b].split())
        if ini is not None and n + k > MAX_PALAVRAS:
            out.append((ini, fim))
            ini, n = None, 0
        ini = a if ini is None else ini
        fim, n = b, n + k
    if ini is not None:
        out.append((ini, fim))
    # tira espaços das bordas para o destaque no painel cair certo
    return [(a + len(texto[a:b]) - len(texto[a:b].lstrip()), b - (len(texto[a:b]) - len(texto[a:b].rstrip())))
            for a, b in out if len(texto[a:b].split()) >= MIN_PALAVRAS]


itens = [(i, a, b) for i, t in enumerate(d["turnos"]) if t["quem"] != "Mediação" for a, b in trechos(t["texto"])]
modelo = SentenceTransformer(MODELO)
X = modelo.encode([f"passage: {d['turnos'][i]['texto'][a:b]}" for i, a, b in itens],
                  normalize_embeddings=True, batch_size=32, show_progress_bar=False)
q8 = np.clip(np.round(X * 127), -127, 127).astype(np.int8)

(site / "data" / "busca.json").write_text(json.dumps({
    "modelo": MODELO, "modelo_navegador": MODELO_NAVEGADOR, "dim": int(X.shape[1]),
    "trechos": [[i, a, b] for i, a, b in itens],
    "vetores": base64.b64encode(q8.tobytes()).decode(),
}, ensure_ascii=False), encoding="utf-8")
print(f"busca: {len(itens)} trechos de {len({i for i, _, _ in itens})} falas, {X.shape[1]} dimensões")

for consulta in sys.argv[2:]:
    q = modelo.encode([f"query: {consulta}"], normalize_embeddings=True)[0]
    s = (q8.astype(np.float32) / 127) @ q
    print(f"\n{consulta!r}")
    vistos = set()
    for k in np.argsort(-s):
        i, a, b = itens[k]
        if i in vistos:
            continue
        vistos.add(i)
        print(f"  {s[k]:.3f} {d['turnos'][i]['quem']:6} {d['turnos'][i]['texto'][a:b][:110]}")
        if len(vistos) == 3:
            break
