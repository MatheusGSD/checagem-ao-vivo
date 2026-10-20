"""Gera a imagem de prévia (og.png, 1200x630) que aparece no card do link no X.

Uso: python make_og.py SITE_DIR
Lê SITE_DIR/data/dados.json e as nuvens de SITE_DIR/img. É a imagem do link no X: o título e as duas
nuvens do painel, que chamam o clique; os números ficam no painel.
Os números usam o mesmo arredondamento do painel (meio para cima, como Math.round do JS).
"""
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

site = Path(sys.argv[1])
d = json.loads((site / "data" / "dados.json").read_text(encoding="utf-8"))
L, F = d["candidatos"]["Lula"], d["candidatos"]["Flávio"]

W, H = 1200, 630
BG, INK, INK2, GRID = "#ffffff", "#121212", "#4f4f4f", "#e2e2e2"
COR = {"Lula": "#e34948", "Flávio": "#2a78d6"}
# mesma linguagem da página: título em serifada (Georgia), números e rótulos em Franklin Gothic
FONT, BOLD, SERIF = "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/framd.ttf", "C:/Windows/Fonts/georgiab.ttf"


def f(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def arred(x):
    return int(math.floor(x + 0.5))


def mmss(s):
    s = arred(s)
    return f"{s // 60}:{s % 60:02d}"


img = Image.new("RGB", (W, H), BG)
dr = ImageDraw.Draw(img)
dr.line((60, 34, W - 60, 34), fill=INK, width=3)
dr.text((60, 48), "DEBATE DO 2º TURNO · BAND · 18/10/2026 · CONFRONTO LIVRE", font=f(22, True), fill=INK2)
dr.text((60, 80), "O que Lula e Flávio disseram no confronto livre", font=ImageFont.truetype(SERIF, 42), fill=INK)

# as duas nuvens do painel (tamanho = frequência; em cor, o que é típico de cada um): é o que chama o clique
larg, y0 = (W - 120 - 40) // 2, 150
for k, (quem, slug) in enumerate((("Lula", "lula"), ("Flávio", "flavio"))):
    x = 60 + k * (larg + 40)
    dr.rounded_rectangle((x, y0 + 6, x + 18, y0 + 24), radius=4, fill=COR[quem])
    dr.text((x + 28, y0), quem, font=f(26, True), fill=INK)
    nv = Image.open(site / "img" / f"nuvem_{slug}.png").convert("RGBA")
    nv = nv.resize((larg, round(nv.height * larg / nv.width)), Image.LANCZOS)
    img.paste(nv, (x, y0 + 40), nv)
dr.line((60, H - 62, W - 60, H - 62), fill=GRID, width=2)
dr.text((60, H - 46), "Perguntas, nomes, relógio, temas e a transcrição completa · painel interativo", font=f(22), fill=INK2)

img.save(site / "og.png", optimize=True)
print("ok ->", site / "og.png")
