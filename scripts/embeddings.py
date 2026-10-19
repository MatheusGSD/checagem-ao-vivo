"""Embeddings de texto: mapa semântico do bloco.

Uso: python embeddings.py TURNOS_FINAL.json SITE_DIR
Lê SITE_DIR/data/dados.json (gerado por analyze.py) e acrescenta "mapa": cada trecho de fala vira um
ponto; trechos com sentido parecido ficam perto. A posição vem só do modelo (aberto, rodando local)
e da projeção UMAP com semente fixa; os temas mostrados em cada ponto são os da fala de origem.

Testado e descartado (com 88 trechos não há sinal estável):
- agrupar trechos em assuntos por k-means: a história mudava com o número de grupos (k=12 juntava
  "bala/criança/matar"; k=7 misturava com Lulinha e voto dos presos);
- ligar perguntas parecidas entre os candidatos: contra 8 pares de controle, o par certo ficou em 1º
  lugar só 2-3 vezes em 8; com perguntas curtas o modelo agrupa pela forma ("Por que você...?").
"""
import json
import re
import sys
from pathlib import Path

from sentence_transformers import SentenceTransformer
from umap import UMAP

MODELO = "intfloat/multilingual-e5-large"
SEMENTE = 7
MIN_PALAVRAS, ALVO_PALAVRAS = 25, 60

src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
site = Path(sys.argv[2])
dados_path = site / "data" / "dados.json"
d = json.loads(dados_path.read_text(encoding="utf-8"))
turns = src["turns"]
t0 = turns[0]["start"]


def trechos(text):
    """Corta a fala em trechos de ~25-60 palavras respeitando o fim das frases."""
    frases = [f.strip() for f in re.split(r"(?<=[.?!])\s+", text) if f.strip()]
    out, cur = [], []
    for f in frases:
        cur.append(f)
        if sum(len(x.split()) for x in cur) >= ALVO_PALAVRAS:
            out.append(" ".join(cur))
            cur = []
    if cur:
        resto = " ".join(cur)
        if out and len(resto.split()) < MIN_PALAVRAS:
            out[-1] += " " + resto
        else:
            out.append(resto)
    return [o for o in out if len(o.split()) >= MIN_PALAVRAS]


pontos = [{"quem": t["speaker"], "ini": round(t["start"] - t0, 1), "temas": t.get("temas", []), "texto": tr}
          for t in turns if t["speaker"] in ("Lula", "Flávio") for tr in trechos(t["text"])]

X = SentenceTransformer(MODELO).encode([f"passage: {p['texto']}" for p in pontos], normalize_embeddings=True,
                                       batch_size=16, show_progress_bar=False)
xy = UMAP(n_neighbors=12, min_dist=0.15, metric="cosine", random_state=SEMENTE).fit_transform(X)
xy = (xy - xy.min(0)) / (xy.max(0) - xy.min(0))
for p, (x, y) in zip(pontos, xy):
    p["x"], p["y"] = round(float(x), 4), round(float(y), 4)

d["mapa"] = {"modelo": MODELO, "pontos": pontos,
             "metodo": f"trechos de {MIN_PALAVRAS} a {ALVO_PALAVRAS} palavras cortados no fim das frases; "
                       f"embeddings {MODELO}; projeção UMAP em 2D (cosseno, semente {SEMENTE})"}
dados_path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"mapa: {len(pontos)} trechos")
