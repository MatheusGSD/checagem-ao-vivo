"""Análises textuais por candidato a partir dos turnos rotulados.

Uso: python analyze.py TURNOS.json SITE_DIR
TURNOS.json: {"meta": {...}, "turns": [{"start", "end", "speaker", "text",
              "tipo"?, "temas"?, "resumo"?}], "afirmacoes"?: [...]}
Gera SITE_DIR/data/dados.json (tudo que o painel usa) e SITE_DIR/img/nuvem_<candidato>.png.
"""
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import spacy
from wordcloud import WordCloud

src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
turns = [t for t in src["turns"] if t["speaker"] in ("Lula", "Flávio", "Mediação")]
site = Path(sys.argv[2])
(site / "data").mkdir(parents=True, exist_ok=True)
(site / "img").mkdir(parents=True, exist_ok=True)
CANDS = ["Lula", "Flávio"]
SLUG = {"Lula": "lula", "Flávio": "flavio"}
# tons da mesma rampa que passam contraste nas superfícies clara e escura do painel
CLOUD_TONES = {"Lula": ["#e34948", "#c93a3a", "#e66767", "#b33232"],
               "Flávio": ["#2a78d6", "#256abf", "#3987e5", "#1c5cab"]}

nlp = spacy.load("pt_core_news_lg", disable=["ner", "parser"])

# muletas de fala e palavras vazias que o spaCy não marca como stopword
EXTRA_STOP = set("""
né aí então gente coisa ser ter estar fazer ir dizer falar vir dar ficar poder querer saber achar
olhar ver haver cá lá aqui ali agora hoje sim não tá tô pra pro senhor senhora candidato candidata
porque quer vai vou eh ah ã hum tipo bom ok vez cada todo tudo algum minuto obrigado olha deixar
""".split())
STOP_SUPERFICIE = nlp.Defaults.stop_words | EXTRA_STOP | {"deixa", "olha", "vai", "vou", "é", "tá", "lhe", "ô"}


def expressoes(words, n_max=4):
    """Sequências de 2 a 4 palavras repetidas, na forma falada, que não começam nem terminam em
    palavra vazia ("polícia federal", "134 milhões", "criança de 5 anos"). Some com a sequência
    menor quando ela só aparece dentro de uma maior com a mesma contagem."""
    cont = Counter()
    for n in range(2, n_max + 1):
        for i in range(len(words) - n + 1):
            g = words[i:i + n]
            if g[0] in STOP_SUPERFICIE or g[-1] in STOP_SUPERFICIE:
                continue
            cont[" ".join(g)] += 1
    rep = {g: v for g, v in cont.items() if v > 1}
    return sorted(((g, v) for g, v in rep.items()
                   if not any(g != h and f" {g} " in f" {h} " and rep[h] == v for h in rep)),
                  key=lambda x: (-x[1], -len(x[0])))

OPP_MENTIONS = {
    "Lula": r"\b(fl[aá]vio|bolsonaro|bolsonarista|bolsonarismo|seu pai|sua fam[ií]lia)\b",
    "Flávio": r"\b(lula|pt|petista|petismo|seu governo)\b",
}
PRONOUNS = {"eu": r"\b(eu|meu|minha|meus|minhas|comigo)\b",
            "nós": r"\b(nós|nosso|nossa|nossos|nossas|vamos|fizemos)\b",
            "você/senhor": r"\b(você|o senhor|a senhora|seu|sua|seus|suas)\b"}


def lemmas(text):
    doc = nlp(text)
    return [t.lemma_.lower() for t in doc
            if t.is_alpha and not t.is_stop and len(t) > 2 and t.lemma_.lower() not in EXTRA_STOP
            and t.pos_ in {"NOUN", "PROPN", "VERB", "ADJ"}]


def mattr(tokens, w=100):
    """Diversidade lexical por janela móvel (menos sensível ao tamanho do texto que o TTR)."""
    if len(tokens) < w:
        return len(set(tokens)) / max(len(tokens), 1)
    return sum(len(set(tokens[i:i + w])) / w for i in range(len(tokens) - w + 1)) / (len(tokens) - w + 1)


t0 = min(t["start"] for t in turns)
res = {"meta": src.get("meta", {}), "inicio_bloco_s": t0,
       "duracao_bloco_s": round(max(t["end"] for t in turns) - t0), "candidatos": {}}
lem = {}
for c in CANDS:
    ts = [t for t in turns if t["speaker"] == c]
    text = " ".join(t["text"] for t in ts)
    low = text.lower()
    words = re.findall(r"\w+", low)
    secs = sum(t["end"] - t["start"] for t in ts)
    lem[c] = lemmas(text)
    por_tipo, por_tema = defaultdict(float), defaultdict(float)
    for t in ts:
        d = t["end"] - t["start"]
        if t.get("tipo"):
            por_tipo[t["tipo"]] += d
        for tema in t.get("temas", []):
            por_tema[tema] += d / len(t["temas"])
    res["candidatos"][c] = {
        "turnos": len(ts),
        "segundos_fala": round(secs),
        "palavras": len(words),
        "palavras_por_minuto": round(len(words) / (secs / 60), 1) if secs else 0,
        "turno_medio_s": round(secs / len(ts), 1) if ts else 0,
        "diversidade_lexical": round(mattr(words), 3),
        "mencoes_adversario": len(re.findall(OPP_MENTIONS[c], low)),
        "pronomes": {k: len(re.findall(p, low)) for k, p in PRONOUNS.items()},
        "perguntas": text.count("?"),
        "top_lemas": Counter(lem[c]).most_common(40),
        "top_bigramas": expressoes(words)[:12],
        "segundos_por_tipo": {k: round(v) for k, v in por_tipo.items()},
        "segundos_por_tema": {k: round(v) for k, v in sorted(por_tema.items(), key=lambda x: -x[1])},
    }

# log-odds com prior de Dirichlet informativo (Monroe, Colaresi & Quinn 2008, "Fightin' Words")
ca, cb = Counter(lem["Lula"]), Counter(lem["Flávio"])
prior = ca + cb
a0, na, nb = sum(prior.values()), sum(ca.values()), sum(cb.values())
scores = []
for w, aw in prior.items():
    if aw < 3:
        continue
    ya, yb = ca[w], cb[w]
    d = (math.log((ya + aw) / (na + a0 - ya - aw)) - math.log((yb + aw) / (nb + a0 - yb - aw)))
    var = 1 / (ya + aw) + 1 / (yb + aw)
    scores.append({"palavra": w, "z": round(d / math.sqrt(var), 2), "Lula": ya, "Flávio": yb})
scores.sort(key=lambda x: x["z"])
res["distintivas"] = {"Lula": [s for s in scores[::-1][:15] if s["z"] > 0],
                      "Flávio": [s for s in scores[:15] if s["z"] < 0]}


def trecho(text, n=180):
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


res["turnos"] = [{"ini": round(t["start"] - t0, 1), "fim": round(t["end"] - t0, 1), "quem": t["speaker"],
                  "palavras": len(t["text"].split()), "tipo": t.get("tipo"), "temas": t.get("temas", []),
                  "resumo": t.get("resumo"), "trecho": trecho(t["text"])} for t in turns]
res["afirmacoes"] = src.get("afirmacoes", [])

for c in CANDS:
    freq = dict(Counter(lem[c]).most_common(120))
    if not freq:
        continue
    rng = random.Random(7)
    tones = CLOUD_TONES[c]
    WordCloud(width=1200, height=760, mode="RGBA", background_color=None, prefer_horizontal=0.92,
              font_path="C:/Windows/Fonts/segoeuib.ttf", random_state=7, margin=6,
              color_func=lambda *a, **k: rng.choice(tones)) \
        .generate_from_frequencies(freq).to_file(str(site / "img" / f"nuvem_{SLUG[c]}.png"))

(site / "data" / "dados.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
for c in CANDS:
    r = res["candidatos"][c]
    print(f"{c}: {r['turnos']} turnos, {r['segundos_fala'] / 60:.1f} min, {r['palavras']} palavras, "
          f"{r['palavras_por_minuto']} ppm, menções ao adversário {r['mencoes_adversario']}")
    print("  distintivas:", ", ".join(s["palavra"] for s in res["distintivas"][c][:12]))
