"""Gera a imagem de prévia (og.png, 1200x630) que aparece no card do link no X.

Uso: python make_og.py SITE_DIR
Lê SITE_DIR/data/dados.json e as nuvens em SITE_DIR/img/.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

site = Path(sys.argv[1])
d = json.loads((site / "data" / "dados.json").read_text(encoding="utf-8"))
L, F = d["candidatos"]["Lula"], d["candidatos"]["Flávio"]

W, H = 1200, 630
BG, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e1e0d9"
LULA, FLAVIO = "#e34948", "#2a78d6"
FONT, BOLD = "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"
f = lambda size, bold=False: ImageFont.truetype(BOLD if bold else FONT, size)

img = Image.new("RGB", (W, H), BG)
dr = ImageDraw.Draw(img)
dr.text((48, 34), "Debate do 2º turno · Band · 18/10/2026", font=f(22), fill=INK2)
dr.text((48, 64), "Lula × Flávio: o confronto livre em números", font=f(44, True), fill=INK)

# nuvens lado a lado
top, ch = 138, 330
for i, (who, color, slug) in enumerate([("Lula", LULA, "lula"), ("Flávio", FLAVIO, "flavio")]):
    x0 = 48 + i * 564
    dr.rounded_rectangle((x0, top, x0 + 14, top + 14), radius=3, fill=color)
    dr.text((x0 + 22, top - 7), who, font=f(22, True), fill=INK)
    cloud = Image.open(site / "img" / f"nuvem_{slug}.png").convert("RGBA")
    cloud.thumbnail((540, ch - 30))
    img.paste(cloud, (x0 + (540 - cloud.width) // 2, top + 28), cloud)

# faixa de números: Lula | rótulo | Flávio
def mmss(s):
    return f"{s // 60}:{s % 60:02d}"


rows = [("Tempo de fala (áudio)", mmss(L["segundos_fala"]), mmss(F["segundos_fala"])),
        ("Palavras por minuto", f"{L['palavras_por_minuto']:.0f}", f"{F['palavras_por_minuto']:.0f}"),
        ("Citou o adversário", f"{L['mencoes_adversario']}", f"{F['mencoes_adversario']}")]
y0 = top + ch + 14
dr.line((48, y0 - 8, W - 48, y0 - 8), fill=GRID, width=2)
cw = (W - 96) // 3
for i, (lab, a, b) in enumerate(rows):
    cx = 48 + i * cw + cw // 2
    dr.text((cx, y0 + 6), lab, font=f(20), fill=INK2, anchor="mt")
    dr.text((cx - 14, y0 + 36), a, font=f(40, True), fill=INK, anchor="rt")
    dr.text((cx, y0 + 40), "×", font=f(30), fill=INK2, anchor="mt")
    dr.text((cx + 14, y0 + 36), b, font=f(40, True), fill=INK, anchor="lt")
    for x, color in [(cx - 14 - dr.textlength(a, font=f(40, True)) - 20, LULA), (cx + 14 + dr.textlength(b, font=f(40, True)) + 8, FLAVIO)]:
        dr.rounded_rectangle((x, y0 + 52, x + 12, y0 + 64), radius=3, fill=color)

img.save(site / "og.png", optimize=True)
print("ok ->", site / "og.png")
