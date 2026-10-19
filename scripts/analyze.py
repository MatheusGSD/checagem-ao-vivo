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

from wordcloud import WordCloud

sys.path.insert(0, str(Path(__file__).parent))  # -P não põe a pasta do script no path
from texto import COMPOSTOS, EXTRA_STOP, nlp, termos  # noqa: E402

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
# versão para o modo escuro: tons mais claros das mesmas rampas, legíveis sobre #1a1a19
CLOUD_TONES_ESCURO = {"Lula": ["#e66767", "#f08a8a", "#eb7a7a", "#f5a3a3"],
                      "Flávio": ["#3987e5", "#6da7ec", "#5598e7", "#86b6ef"]}

STOP_SUPERFICIE = nlp.Defaults.stop_words | EXTRA_STOP | {"deixa", "olha", "vai", "vou", "é", "tá", "lhe", "ô"}
# palavra que nunca aparece em minúscula na transcrição é nome próprio ("Roberta Luchsinger", "Brasil")
_minusc_todas = {w for t in src["turns"] for w in re.findall(r"\b[a-záéíóúâêôãõç]+\b", t["text"])}


def nomes_proprios(expr):
    for _, disp in COMPOSTOS:  # nomes compostos na forma oficial ("Polícia Federal", "Bolsa Família")
        expr = re.sub(re.escape(disp.lower()), disp, expr)
    return " ".join(w[:1].upper() + w[1:] if w.isalpha() and w.islower() and w not in _minusc_todas else w
                    for w in expr.split())


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

# quantas vezes cada um disse o nome do adversário. Só o nome: "Bolsonaro" na boca de Lula quase sempre
# é o pai, e "Instituto Lula" é o nome de uma instituição, então nenhum dos dois entra.
NOME_ADV = {"Lula": r"\bfl[aá]vio\b", "Flávio": r"\blula\b"}
NOME_ADV_FORA = {"Lula": None, "Flávio": r"\binstituto lula\b"}
PRONOUNS = {"eu": r"\b(eu|meu|minha|meus|minhas|comigo)\b",
            "nós": r"\b(nós|nosso|nossa|nossos|nossas|vamos|fizemos)\b",
            "você/senhor": r"\b(você|o senhor|a senhora|seu|sua|seus|suas)\b"}




def mattr(tokens, w=100):
    """Diversidade lexical por janela móvel (menos sensível ao tamanho do texto que o TTR)."""
    if len(tokens) < w:
        return len(set(tokens)) / max(len(tokens), 1)
    return sum(len(set(tokens[i:i + w])) / w for i in range(len(tokens) - w + 1)) / (len(tokens) - w + 1)


t0 = min(t["start"] for t in turns)
# o áudio gravado bate segundo a segundo com o vídeo da live no YouTube: inicio_bloco_s + ini abre a fala no vídeo
res = {"meta": src.get("meta", {}), "video_youtube": "Gqc1rQtzWUk", "inicio_bloco_s": t0,
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
        "nome_adversario": len(re.findall(NOME_ADV[c], low)) - (len(re.findall(NOME_ADV_FORA[c], low)) if NOME_ADV_FORA[c] else 0),
        "pronomes": {k: len(re.findall(p, low)) for k, p in PRONOUNS.items()},
        "perguntas": text.count("?"),
        "top_lemas": [(w, n) for w, n in lem[c].most_common(40) if n >= MIN_OCORR],
        "top_bigramas": [(nomes_proprios(g), n) for g, n in expressoes(words)[:12]],
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

# cada palavra típica herda o tema das falas do candidato em que ela aparece (votos ponderados
# pelas ocorrências; um turno com dois temas divide o voto). Junta "bala", "criança", "matar"
# sob o mesmo assunto em vez de parecerem três coisas diferentes.
termos_turno = [(t, Counter({exibe.get(w, w): n for w, n in termos(t["text"])[0].items()}))
                for t in turns if t["speaker"] in CANDS]
for c, lista in res["distintivas"].items():
    for s in lista:
        votos = Counter()
        for t, cont in termos_turno:
            if t["speaker"] == c and cont[s["palavra"]] and t.get("temas"):
                for tema in t["temas"]:
                    votos[tema] += cont[s["palavra"]] / len(t["temas"])
        s["tema"] = votos.most_common(1)[0][0] if votos else None


def trecho(text, n=180):
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


res["turnos"] = [{"ini": round(t["start"] - t0, 1), "fim": round(t["end"] - t0, 1), "quem": t["speaker"],
                  "palavras": len(t["text"].split()), "tipo": t.get("tipo"), "temas": t.get("temas", []),
                  "resumo": t.get("resumo"), "trecho": trecho(t["text"]), "texto": t["text"]} for t in turns]
res["afirmacoes"] = src.get("afirmacoes", [])
res["perguntas"] = src.get("perguntas", [])
res["pipeline"] = {**src.get("pipeline", {}), "turnos": len(turns)}

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
    rng_escuro = random.Random(7)  # mesma disposição, só troca as cores
    wc.recolor(color_func=lambda *a, **k: rng_escuro.choice(CLOUD_TONES_ESCURO[c]))
    wc.to_file(str(site / "img" / f"nuvem_{SLUG[c]}_escuro.png"))
print(f"  nuvens: letra máxima {letra_max}px para {gmax} ocorrências; "
      f"{', '.join(f'{c} {len(w.layout_)} termos' for c, w in clouds.items())}")

(site / "data" / "dados.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
for c in CANDS:
    r = res["candidatos"][c]
    print(f"{c}: {r['turnos']} turnos, {r['segundos_fala'] / 60:.1f} min, {r['palavras']} palavras, "
          f"{r['palavras_por_minuto']} ppm, disse o nome do adversário {r['nome_adversario']} vezes")
    print("  distintivas:", ", ".join(s["palavra"] for s in res["distintivas"][c][:12]))
