"""Junta transcrição + rótulos de voz + correções manuais em turnos de fala.

Uso: python build_turns.py TRANSCRICAO.json ROTULOS_AUTO.json CORRECOES.json SAIDA.json
Segmentos consecutivos do mesmo locutor (pausa < 4 s) viram um turno.
"""
import json
import re
import sys
from pathlib import Path

trans_doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
trans = trans_doc["segments"]
auto = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
corr = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
NOME = {"L": "Lula", "F": "Flávio", "M": "Mediação"}

labels = list(auto)
for i, lab in corr["rotulos_manuais"].items():
    labels[int(i)] = lab
subs = [(re.compile(a), b) for a, b in corr["normalizacao"]]
# glossário de grafia (nomes conferidos em fonte pública), aplicado depois das regras de nomes
glossario = Path(sys.argv[3]).with_name("glossario.json")
gloss = ([(re.compile(e["errado"]), e["certo"]) for e in json.loads(glossario.read_text(encoding="utf-8"))["entradas"]]
         if glossario.exists() else [])
subs += gloss


def glossa(obj):
    """Aplica o glossário também aos textos da classificação (trechos citados, resumos, títulos)."""
    if isinstance(obj, str):
        for pat, rep in gloss:
            obj = pat.sub(rep, obj)
        return obj
    if isinstance(obj, list):
        return [glossa(x) for x in obj]
    if isinstance(obj, dict):
        return {k: glossa(v) for k, v in obj.items()}
    return obj


def normaliza(text):
    for pat, rep in subs:
        text = pat.sub(rep, text)
    return text


def resolve_pergunta(turns, quem, rel, literal, aparte_max=12):
    """Acha o turno da pergunta (pelo segundo de início) e confere que o trecho é literal. A fala
    seguinte é tudo o que o outro candidato disse até quem perguntou retomar a palavra com uma
    fala de `aparte_max` palavras ou mais (apartes curtos de quem perguntou não interrompem)."""
    t0 = turns[0]["start"]
    i = min(range(len(turns)), key=lambda j: abs(turns[j]["start"] - t0 - rel))
    if turns[i]["speaker"] != quem or abs(turns[i]["start"] - t0 - rel) > 1.5:
        raise SystemExit(f"pergunta de {quem} em {rel}s não bate com o turno {turns[i]['start'] - t0:.0f}s")
    # o agente lê o texto já com a caixa corrigida; a correção não muda o comprimento, então a posição vale
    # para as duas formas
    for txt in (turns[i]["text"], caixa(turns[i])):
        if literal in txt:
            off = txt.index(literal)
            break
    else:
        raise SystemExit(f"trecho não é literal no turno {rel}s: {literal!r}")
    resp = []
    for t in turns[i + 1:]:
        if t["speaker"] == quem and len(t["text"].split()) >= aparte_max:
            break
        if t["speaker"] not in (quem, "Mediação"):
            resp.append(t)
    return {"t": rel, "pergunta": literal,
            "t_resposta": round(resp[0]["start"] - t0) if resp else None,
            "resposta": " ".join(t["text"] for t in resp) or None,
            "_i": i, "_off": off, "_resp": [turns.index(t) for t in resp]}


turns = []
for seg, lab in zip(trans, labels):
    if lab == "?":
        raise SystemExit(f"segmento sem rótulo em {seg['start']}: {seg['text']}")
    who, text = NOME[lab], normaliza(seg["text"])
    if turns and turns[-1]["speaker"] == who and seg["start"] - turns[-1]["end"] < 4:
        turns[-1]["end"] = seg["end"]
        turns[-1]["_juncoes"].append(len(turns[-1]["text"]) + 1)  # onde começa o segmento emendado
        turns[-1]["text"] += " " + text
    else:
        turns.append({"start": seg["start"], "end": seg["end"], "speaker": who, "text": text, "_juncoes": []})

# O Whisper começa cada segmento com maiúscula; ao emendar segmentos de uma mesma frase isso deixa
# maiúsculas soltas ("No governo Bolsonaro E vai ser"), mais nas falas quebradas em mais segmentos.
# Na junção sem fim de frase, a primeira palavra vai para minúscula se ela aparece em minúscula em
# algum ponto da transcrição (palavra comum, não nome próprio) e não vem seguida de outra palavra com
# maiúscula ("Banco Central", "Polícia Federal" ficam). Só muda a caixa (o comprimento do texto não
# muda), então as posições dos trechos citados continuam valendo.
_minusc = {w for s in trans for w in re.findall(r"\b[a-záéíóúâêôãõç]+\b", normaliza(s["text"]))}


def caixa(t):
    txt = list(t["text"])
    for p in t["_juncoes"]:
        antes = t["text"][:p - 1].rstrip()
        m = re.match(r"(\w+)(?:\W+(\w+))?", t["text"][p:])
        if not m or re.search(r"[.?!…:]$", antes) or not txt[p].isupper():
            continue
        palavra, seguinte = m.group(1), m.group(2) or ""
        if ((len(palavra) == 1 or palavra[1:].islower()) and palavra.lower() in _minusc
                and not seguinte[:1].isupper()):
            txt[p] = txt[p].lower()
    return "".join(txt)

out = {"turns": turns,
       "pipeline": {"modelo_transcricao": trans_doc.get("model") or "large-v3-turbo", "segmentos": len(trans), "rotulos_corrigidos": len(corr["rotulos_manuais"]),
                    "regras_nomes": len(corr["normalizacao"]), "glossario": len(gloss), "rotulos_por": corr.get("agente") or "conversa"}}
if len(sys.argv) > 5:
    # classificação manual/IA, indexada pelo segundo de início do turno dentro do bloco
    cls = glossa(json.loads(Path(sys.argv[5]).read_text(encoding="utf-8")))
    t0 = turns[0]["start"]
    keys = {float(k): v for k, v in cls["turnos"].items()}
    tolerancia = 0.15 if cls.get("agente") else 1.5  # agente: chave ao décimo de segundo; conversa: segundo inteiro
    faltando = []
    for t in turns:
        rel = t["start"] - t0
        k = min(keys, key=lambda k: abs(k - rel))
        if abs(k - rel) > tolerancia:
            faltando.append(f"{rel:.0f}s {t['speaker']}: {t['text'][:60]}")
            continue
        t["tipo"], t["temas"], t["resumo"] = keys.pop(k)
    if faltando or keys:
        raise SystemExit("turnos sem classificação:\n" + "\n".join(faltando) +
                         (f"\nchaves sem turno: {sorted(keys)}" if keys else ""))
    out["pipeline"]["classificacao_por"] = cls.get("agente") or "conversa"
    out.update(meta=cls["meta"], afirmacoes=cls["afirmacoes"],
               perguntas=[{"quem": f["quem"], "tema": f["tema"],
                           "instancias": [resolve_pergunta(turns, f["quem"], t, lit) for t, lit in f["instancias"]]}
                          for f in cls.get("fios_perguntas", [])])

# aplica a correção de caixa depois de todas as validações e remapeia os trechos pelas mesmas posições
originais = [t["text"] for t in turns]
for t in turns:
    t["text"] = caixa(t)
    del t["_juncoes"]
out["pipeline"]["caixa_corrigida"] = sum(a != t["text"] for a, t in zip(originais, turns))
for f in out.get("perguntas", []):
    for it in f["instancias"]:
        i, off = it.pop("_i"), it.pop("_off")
        it["pergunta"] = turns[i]["text"][off:off + len(it["pergunta"])]
        r = it.pop("_resp")
        it["resposta"] = " ".join(turns[j]["text"] for j in r) or None
t0 = turns[0]["start"]
for a in out.get("afirmacoes", []):
    i = min(range(len(turns)), key=lambda j: abs(turns[j]["start"] - t0 - a["t"]))
    if a.get("trecho") and a["trecho"] in originais[i]:
        off = originais[i].index(a["trecho"])
        a["trecho"] = turns[i]["text"][off:off + len(a["trecho"])]

Path(sys.argv[4]).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for who in NOME.values():
    ts = [t for t in turns if t["speaker"] == who]
    print(f"{who}: {len(ts)} turnos, {sum(t['end'] - t['start'] for t in ts) / 60:.1f} min")
