"""Gera a imagem de prévia (og.png, 1200x630) que aparece no card do link no X.

Uso: python make_og.py SITE_DIR
Lê SITE_DIR/data/dados.json. O card vira miniatura de ~500 px no feed, então só leva o que se lê
nesse tamanho: o título e três comparações com números grandes e o nome de cada candidato escrito.
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
dr.text((60, 84), "Lula × Flávio: o confronto livre em números", font=ImageFont.truetype(SERIF, 46), fill=INK)

linhas = [("Tempo de fala (medido no áudio)", mmss(L["segundos_fala"]), mmss(F["segundos_fala"])),
          ("Palavras por minuto", str(arred(L["palavras_por_minuto"])), str(arred(F["palavras_por_minuto"]))),
          ("Vezes que citou o adversário", str(L["mencoes_adversario"]), str(F["mencoes_adversario"]))]
y = 196
meio = W // 2
for rot, a, b in linhas:
    dr.line((60, y - 14, W - 60, y - 14), fill=GRID, width=2)
    dr.text((meio, y), rot, font=f(26), fill=INK2, anchor="mt")
    # Lula à esquerda do centro (alinhado à direita), Flávio à direita (alinhado à esquerda)
    num = f(72, True)
    dr.text((meio - 40, y + 36), a, font=num, fill=INK, anchor="rt")
    dr.text((meio + 40, y + 36), b, font=num, fill=INK, anchor="lt")
    for quem, x_txt, anc in (("Lula", meio - 40 - dr.textlength(a, font=num) - 24, "rt"),
                             ("Flávio", meio + 40 + dr.textlength(b, font=num) + 24, "lt")):
        nome = f(30, True)
        larg = dr.textlength(quem, font=nome)
        x_sw = x_txt - larg - 30 if anc == "rt" else x_txt
        x_nome = x_txt if anc == "rt" else x_txt + 30
        dr.rounded_rectangle((x_sw, y + 72, x_sw + 20, y + 92), radius=4, fill=COR[quem])
        dr.text((x_nome, y + 66), quem, font=nome, fill=INK, anchor="rt" if anc == "rt" else "lt")
    y += 134
dr.text((meio, H - 34), "Perguntas, temas, palavras e a transcrição completa no painel", font=f(24), fill=INK2, anchor="mb")

img.save(site / "og.png", optimize=True)
print("ok ->", site / "og.png")
