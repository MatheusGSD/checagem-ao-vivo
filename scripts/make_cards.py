"""Gera as imagens do fio no X (1200x675, 16:9), uma por post, a partir de SITE_DIR/data/dados.json.

Uso: python make_cards.py SITE_DIR
Saída em SITE_DIR/cards/. Cada imagem se lê sozinha no feed: uma frase com o achado, o gráfico mínimo que
a sustenta e a fonte no rodapé. Mesmas cores e arredondamento do painel.
"""
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

site = Path(sys.argv[1])
d = json.loads((site / "data" / "dados.json").read_text(encoding="utf-8"))
L, F = d["candidatos"]["Lula"], d["candidatos"]["Flávio"]
out = site / "cards"
out.mkdir(exist_ok=True)

W, H = 1200, 675
BG, INK, INK2, MUTED, GRID = "#ffffff", "#121212", "#4f4f4f", "#727272", "#e2e2e2"
COR = {"Lula": "#e34948", "Flávio": "#2a78d6"}
# títulos em serifada editorial (Georgia), rótulos e números em Franklin Gothic, como na página
FONT, BOLD, SERIF = "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/framd.ttf", "C:/Windows/Fonts/georgiab.ttf"
X0 = 64  # margem


def f(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def arred(x):
    return int(math.floor(x + 0.5))


def quebra(dr, texto, fonte, larg):
    linhas, cur = [], ""
    for p in texto.split():
        t = (cur + " " + p).strip()
        if dr.textlength(t, font=fonte) <= larg:
            cur = t
        else:
            linhas.append(cur)
            cur = p
    return linhas + [cur]


def card(titulo, nota):
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    dr.line((X0, 30, W - X0, 30), fill=INK, width=3)
    dr.text((X0, 44), "DEBATE DO 2º TURNO · BAND · CONFRONTO LIVRE", font=f(18, True), fill=INK2)
    dr.text((W - X0, 44), "matheusgsd.github.io/debate-lula-flavio", font=f(18), fill=INK2, anchor="ra")
    y = 78
    serif = ImageFont.truetype(SERIF, 46)
    for ln in quebra(dr, titulo, serif, W - 2 * X0):
        dr.text((X0, y), ln, font=serif, fill=INK)
        y += 56
    dr.line((X0, H - 58, W - X0, H - 58), fill=GRID, width=2)
    dr.text((X0, H - 44), nota, font=f(19), fill=MUTED)
    return img, dr, y + 18


def legenda(dr, x, y):
    for quem in ("Lula", "Flávio"):
        dr.rounded_rectangle((x, y + 4, x + 18, y + 22), radius=4, fill=COR[quem])
        dr.text((x + 26, y), quem, font=f(22, True), fill=INK)
        x += 26 + dr.textlength(quem, font=f(22, True)) + 30


def barras(dr, y, linhas, fmt, maximo, rot_larg=250, alt=22):
    """Barras agrupadas: para cada rótulo, uma barra de Lula e uma de Flávio, mesma escala."""
    x_b, x_max = X0 + rot_larg + 16, W - X0 - 70
    for rot, a, b in linhas:
        dr.text((X0 + rot_larg, y + alt), rot, font=f(22), fill=INK, anchor="rm")
        for k, (quem, v) in enumerate((("Lula", a), ("Flávio", b))):
            yy = y + k * (alt + 4)
            larg = max(3, (x_max - x_b) * v / maximo)
            dr.rounded_rectangle((x_b, yy, x_b + larg, yy + alt), radius=4, fill=COR[quem], corners=(False, True, True, False))
            dr.text((x_b + larg + 8, yy + alt / 2), fmt(v), font=f(18), fill=INK2, anchor="lm")
        y += 2 * alt + 4 + 16
    return y


def salva(img, nome):
    img.save(out / nome, optimize=True)
    print("ok ->", out / nome)


# 1. perguntas diretas
P = d["perguntas"]
n = {q: sum(len(p["instancias"]) for p in P if p["quem"] == q) for q in ("Lula", "Flávio")}
razao = n["Lula"] / n["Flávio"]
comp = "quase o dobro de" if 1.8 <= razao < 2 else "o dobro de" if 2 <= razao < 2.2 else "mais"
img, dr, y = card(f"Lula fez {comp} perguntas diretas que Flávio: {n['Lula']} a {n['Flávio']}",
                  "Perguntas diretas ao adversário, agrupadas por assunto. Contagem: agente Claude, trechos conferidos no texto")
y += 10
col = (W - 2 * X0 - 60) // 2
for quem, x in (("Lula", X0), ("Flávio", X0 + col + 60)):
    dr.rounded_rectangle((x, y + 22, x + 14, y + 100), radius=4, fill=COR[quem])
    dr.text((x + 28, y), str(n[quem]), font=f(96, True), fill=INK)
    dr.text((x + 28 + dr.textlength(str(n[quem]), font=f(96, True)) + 16, y + 30), f"perguntas de {quem}", font=f(26, True), fill=INK)
    yy = y + 128
    dr.text((x, yy), "Os assuntos mais repetidos:", font=f(20), fill=MUTED)
    yy += 34
    for p in sorted((p for p in P if p["quem"] == quem), key=lambda p: -len(p["instancias"]))[:4]:
        k = len(p["instancias"])
        dr.text((x, yy), f"{k}×", font=f(24, True), fill=INK)
        for j, ln in enumerate(quebra(dr, p["tema"], f(24), col - 60)[:1]):
            dr.text((x + 54, yy), ln, font=f(24), fill=INK)
        yy += 38
salva(img, "1_perguntas.png")

# 2. citou o adversário e "você"
mil = lambda c, k: c["pronomes"][k] / c["palavras"] * 1000
lula_nome = next((s["Flávio"] for s in d["distintivas"]["Flávio"] if s["palavra"] == "Lula"), None)
img, dr, y = card(f"Flávio citou Lula {F['mencoes_adversario']} vezes. Lula citou Flávio {L['mencoes_adversario']}.",
                  "Citação: nome, sobrenome ou partido do adversário, ou expressões como \"seu pai\". Contagem por código")
y += 30
meio = W // 2
for quem, c, x, anc in (("Lula", L, meio - 60, "ra"), ("Flávio", F, meio + 60, "la")):
    dr.text((x, y), str(c["mencoes_adversario"]), font=f(150, True), fill=INK, anchor=anc)
    larg = dr.textlength(quem, font=f(30, True))
    x_sw = x - larg - 30 if anc == "ra" else x
    dr.rounded_rectangle((x_sw, y + 196, x_sw + 20, y + 216), radius=4, fill=COR[quem])
    dr.text((x - 0 if anc == "ra" else x + 30, y + 190), quem, font=f(30, True), fill=INK, anchor=anc)
dr.line((meio, y + 20, meio, y + 220), fill=GRID, width=2)
y += 260
txt = (f"Flávio disse \"Lula\" {lula_nome} vezes. " if lula_nome else "") + \
      f"A cada mil palavras, Flávio disse \"você\" {arred(mil(F, 'você/senhor'))} vezes; Lula, {arred(mil(L, 'você/senhor'))}."
for ln in quebra(dr, txt, f(26), W - 2 * X0):
    dr.text((meio, y), ln, font=f(26), fill=INK2, anchor="ma")
    y += 36
salva(img, "2_citacoes.png")

# 3. tom: ataque, defesa, proposta
TIPOS = [("ataque", "Ataque"), ("defesa", "Defesa / resposta"), ("proposta", "Proposta"), ("outro", "Outro")]
pct = lambda c, k: (c["segundos_por_tipo"].get(k, 0) / c["segundos_fala"] * 100) if c["segundos_fala"] else 0
img, dr, y = card(f"{arred(pct(F, 'ataque'))}% do tempo de Flávio foi ataque ao adversário. No de Lula, {arred(pct(L, 'ataque'))}%.",
                  "Parcela do tempo de fala. Cada fala classificada por agente Claude com prompt público; pode ter erro")
legenda(dr, X0 + 266, y)
y += 46
barras(dr, y, [(nm, pct(L, k), pct(F, k)) for k, nm in TIPOS], lambda v: f"{arred(v)}%", 100)
salva(img, "3_tom.png")

# 4. palavras típicas
img, dr, y = card("As palavras que cada um usou muito mais que o outro",
                  "Log-odds com prior informativo (Monroe et al., 2008). Entre parênteses: vezes que Lula × Flávio disseram")
y += 14
col = (W - 2 * X0 - 40) // 2
for k, (quem, x) in enumerate((("Lula", X0), ("Flávio", X0 + col + 40))):
    dr.rounded_rectangle((x, y + 6, x + 18, y + 24), radius=4, fill=COR[quem])
    dr.text((x + 28, y), quem, font=f(26, True), fill=INK)
    yy = y + 50
    for s in d["distintivas"][quem][:7]:
        dr.text((x, yy), s["palavra"], font=f(30, True), fill=INK)
        dr.text((x + dr.textlength(s["palavra"], font=f(30, True)) + 12, yy + 8), f"({s['Lula']} × {s['Flávio']})", font=f(20), fill=MUTED)
        yy += 44
salva(img, "4_palavras.png")

# 5. temas
temas = sorted(set(L["segundos_por_tema"]) | set(F["segundos_por_tema"]),
               key=lambda t: -(L["segundos_por_tema"].get(t, 0) + F["segundos_por_tema"].get(t, 0)))[:6]
tp = lambda c, t: c["segundos_por_tema"].get(t, 0) / c["segundos_fala"] * 100
img, dr, y = card("O caso Vorcaro foi o tema nº 1 dos dois candidatos" if temas[0] == "Caso Vorcaro"
                  else f"O tema que mais ocupou o confronto: {temas[0]}",
                  "Parcela do tempo de fala por tema (uma fala com dois temas divide o tempo). Temas: agente Claude")
legenda(dr, X0 + 296, y)
y += 40
barras(dr, y, [(t, tp(L, t), tp(F, t)) for t in temas], lambda v: f"{arred(v)}%",
       max(max(tp(L, t), tp(F, t)) for t in temas) * 1.1, rot_larg=280, alt=16)
salva(img, "5_temas.png")
