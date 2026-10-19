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


# nomes e expressões que contam como um termo só (regex sem distinção de maiúsculas -> forma exibida)
COMPOSTOS = [
    (r"careca do inss|careca", "Careca do INSS"),
    (r"roberta luchsinger|luchsinger|roberta", "Roberta Luchsinger"),
    (r"daniel vorcaro|vorcaro|daniel", "Daniel Vorcaro"),  # "Daniel" sozinho também é ele ("ó, Daniel, não vende o banco")
    (r"fl[aá]vio bolsonaro", "Flávio Bolsonaro"),  # separa o candidato de "Bolsonaro" (o pai)
    (r"pol[ií]cia federal", "Polícia Federal"),
    (r"bolsa fam[ií]lia", "Bolsa Família"),
    (r"banco central", "Banco Central"),
    (r"instituto lula", "Instituto Lula"),
    (r"escrit[oó]rio do crime", "Escritório do Crime"),
    (r"escala 6x1", "escala 6x1"),
    (r"andr[eé] mendon[cç]a", "André Mendonça"),
    (r"frei chico", "Frei Chico"),
    (r"jaques wagner", "Jaques Wagner"),
    (r"rui costa", "Rui Costa"),
    (r"crime organizado", "crime organizado"),
    (r"lavagem de dinheiro", "lavagem de dinheiro"),
    (r"fake news", "fake news"),
    (r"estados unidos", "Estados Unidos"),
    (r"congresso nacional", "Congresso Nacional"),
    (r"licen[cç]a[- ]de[- ]maternidade|licen[cç]a[- ]maternidade|aux[ií]lio de maternidade", "licença-maternidade"),
    (r"p[eé][- ]de[- ]meia", "Pé-de-Meia"),
    (r"terras ind[ií]genas", "terras indígenas"),
    (r"vini j[uú]nior", "Vini Jr."),
]
# números com unidade viram termo ("134 milhões", "73%")
NUMERO = re.compile(r"(?:R\$\s*)?\b(\d+(?:[.,]\d+)?)\s*(%|mil\b|milh(?:ão|ões)\b|bilh(?:ão|ões)\b|reais\b|mentiras\b)", re.I)
SIGLA = re.compile(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,5}\b")  # PT, PL, STF, INSS...
CLASSES = {"NOUN", "PROPN", "VERB", "ADJ"}
# frases-muleta removidas antes da contagem ("deixa eu lhe contar uma coisa")
MULETAS_FRASE = re.compile(
    r"\b(?:deixa eu (?:lhe |te )?(?:contar|falar|dizer|perguntar)|(?:eu )?vou (?:te |lhe )?(?:falar|contar|dizer))"
    r"(?: uma coisa)?(?: (?:pra|para) (?:você|vocês))?|,\s*cara\b", re.I)
# formas que o lematizador do spaCy deixa de reduzir (imperativos no começo da frase)
LEMA_FIX = {"finger": "fingir", "explica": "explicar", "diga": "dizer", "conta": "contar", "fala": "falar", "olha": "olhar"}


def termos(text):
    """Conta os termos de um texto. Ordem: expressões compostas, números com unidade e siglas saem
    primeiro (inteiros); o resto passa pelo spaCy e fica o lema de substantivos, nomes próprios,
    verbos e adjetivos que não sejam palavra vazia nem muleta. Devolve (Counter, nome_proprio)."""
    cont, proprio = Counter(), Counter()
    text = MULETAS_FRASE.sub(" ", text)
    for pat, disp in COMPOSTOS:
        rx = re.compile(rf"\b(?:{pat})\b", re.I)
        cont[disp] += len(rx.findall(text))
        text = rx.sub(" ", text)
    for m in NUMERO.finditer(text):
        num, uni = m.group(1), m.group(2).lower()
        cont[f"{num}%" if uni == "%" else f"{num} {uni}"] += 1
    text = NUMERO.sub(" ", text)
    for m in SIGLA.finditer(text):
        cont[m.group(0)] += 1
    text = SIGLA.sub(" ", text)
    for t in nlp(text):
        palavra = t.is_alpha or ("-" in t.text and all(p.isalpha() for p in t.text.split("-")))
        lema = t.lemma_.lower() if t.pos_ == "NOUN" else LEMA_FIX.get(t.lemma_.lower(), t.lemma_.lower())
        if (palavra and not t.is_stop and len(t) > 2 and lema not in EXTRA_STOP and t.pos_ in CLASSES):
            cont[lema] += 1
            proprio[lema] += t.pos_ == "PROPN"
    return +cont, proprio


def mattr(tokens, w=100):
    """Diversidade lexical por janela móvel (menos sensível ao tamanho do texto que o TTR)."""
    if len(tokens) < w:
        return len(set(tokens)) / max(len(tokens), 1)
    return sum(len(set(tokens[i:i + w])) / w for i in range(len(tokens) - w + 1)) / (len(tokens) - w + 1)


t0 = min(t["start"] for t in turns)
res = {"meta": src.get("meta", {}), "inicio_bloco_s": t0,
       "duracao_bloco_s": round(max(t["end"] for t in turns) - t0), "candidatos": {}}
lem, proprio = {}, Counter()
for c in CANDS:
    lem[c], p = termos(" ".join(t["text"] for t in turns if t["speaker"] == c))
    proprio += p
# nome próprio (marcado como tal na maioria das ocorrências) aparece com inicial maiúscula
total = lem["Lula"] + lem["Flávio"]
exibe = {w: (w[:1].upper() + w[1:] if proprio[w] * 2 > total[w] else w) for w in total}
lem = {c: Counter({exibe[w]: n for w, n in lem[c].items()}) for c in CANDS}
MIN_OCORR = 2

for c in CANDS:
    ts = [t for t in turns if t["speaker"] == c]
    text = " ".join(t["text"] for t in ts)
    low = text.lower()
    words = re.findall(r"\w+", low)
    secs = sum(t["end"] - t["start"] for t in ts)
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
        "top_lemas": [(w, n) for w, n in lem[c].most_common(40) if n >= MIN_OCORR],
        "top_bigramas": expressoes(words)[:12],
        "segundos_por_tipo": {k: round(v) for k, v in por_tipo.items()},
        "segundos_por_tema": {k: round(v) for k, v in sorted(por_tema.items(), key=lambda x: -x[1])},
    }

# log-odds com prior de Dirichlet informativo (Monroe, Colaresi & Quinn 2008, "Fightin' Words")
ca, cb = lem["Lula"], lem["Flávio"]
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
res["perguntas"] = src.get("perguntas", [])

res["criterio_nuvem"] = {
    "classes": "substantivos, nomes próprios, verbos e adjetivos (na forma de dicionário)",
    "inteiros": [d for _, d in COMPOSTOS],
    "tambem_entram": "siglas (PT, PL, STF, INSS...), palavras com hífen e números com unidade (\"134 milhões\", \"73%\")",
    "minimo_ocorrencias": MIN_OCORR,
    "muletas_excluidas": sorted(EXTRA_STOP),
    "frases_excluidas": "\"deixa eu (lhe/te) contar/falar/dizer/perguntar (uma coisa)\", \"vou (te/lhe) falar/contar/dizer\" "
                        "e o vocativo \"cara\" depois de vírgula (\"é só isso, cara\")",
    "escala": "mesma escala nas duas nuvens; a letra cresce com a raiz quadrada da contagem, "
              "para que a área ocupada seja proporcional a quantas vezes a palavra foi dita",
}

# mesma escala nas duas nuvens: a maior contagem geral define a letra máxima, e a letra de cada
# palavra é proporcional à raiz da contagem (relative_scaling=1 sobre sqrt -> área ~ contagem)
gmax = max(max(lem[c].values()) for c in CANDS)


def nuvem(c, letra_max):
    freq = {w: math.sqrt(n) for w, n in lem[c].items() if n >= MIN_OCORR}
    rng = random.Random(7)
    tones = CLOUD_TONES[c]
    return WordCloud(width=1200, height=760, mode="RGBA", background_color=None, prefer_horizontal=0.92,
                     font_path="C:/Windows/Fonts/segoeuib.ttf", random_state=7, margin=6,
                     relative_scaling=1, max_words=len(freq), min_font_size=8,
                     max_font_size=round(letra_max * math.sqrt(max(lem[c].values()) / gmax)),
                     color_func=lambda *a, **k: rng.choice(tones)).generate_from_frequencies(freq)


def proporcional(wc, c, k):
    """Toda palavra coube com letra ~ k * raiz(contagem)? (1 px de folga pelo arredondamento)"""
    n_freq = sum(1 for n in lem[c].values() if n >= MIN_OCORR)
    return len(wc.layout_) == n_freq and all(abs(fs - k * math.sqrt(lem[c][w])) <= 1.5
                                             for (w, _), fs, *_ in wc.layout_)


# a biblioteca encolhe as palavras seguintes quando uma não cabe; baixa a letra máxima até as
# duas nuvens caberem inteiras na escala certa
for letra_max in range(240, 60, -10):
    k = letra_max / math.sqrt(gmax)
    clouds = {c: nuvem(c, letra_max) for c in CANDS}
    if all(proporcional(clouds[c], c, k) for c in CANDS):
        break
else:
    raise SystemExit("nenhuma letra máxima coube na escala; aumente a área da nuvem")
for c, wc in clouds.items():
    wc.to_file(str(site / "img" / f"nuvem_{SLUG[c]}.png"))
print(f"  nuvens: letra máxima {letra_max}px para {gmax} ocorrências; "
      f"{', '.join(f'{c} {len(w.layout_)} termos' for c, w in clouds.items())}")

(site / "data" / "dados.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
for c in CANDS:
    r = res["candidatos"][c]
    print(f"{c}: {r['turnos']} turnos, {r['segundos_fala'] / 60:.1f} min, {r['palavras']} palavras, "
          f"{r['palavras_por_minuto']} ppm, menções ao adversário {r['mencoes_adversario']}")
    print("  distintivas:", ", ".join(s["palavra"] for s in res["distintivas"][c][:12]))
