"""Junta transcrição + rótulos de voz + correções manuais em turnos de fala.

Uso: python build_turns.py TRANSCRICAO.json ROTULOS_AUTO.json CORRECOES.json SAIDA.json
Segmentos consecutivos do mesmo locutor (pausa < 4 s) viram um turno.
"""
import json
import re
import sys
from pathlib import Path

trans = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["segments"]
auto = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
corr = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
NOME = {"L": "Lula", "F": "Flávio", "M": "Mediação"}

labels = list(auto)
for i, lab in corr["rotulos_manuais"].items():
    labels[int(i)] = lab
subs = [(re.compile(a), b) for a, b in corr["normalizacao"]]


def normaliza(text):
    for pat, rep in subs:
        text = pat.sub(rep, text)
    return text


turns = []
for seg, lab in zip(trans, labels):
    if lab == "?":
        raise SystemExit(f"segmento sem rótulo em {seg['start']}: {seg['text']}")
    who, text = NOME[lab], normaliza(seg["text"])
    if turns and turns[-1]["speaker"] == who and seg["start"] - turns[-1]["end"] < 4:
        turns[-1]["end"] = seg["end"]
        turns[-1]["text"] += " " + text
    else:
        turns.append({"start": seg["start"], "end": seg["end"], "speaker": who, "text": text})

out = {"turns": turns}
if len(sys.argv) > 5:
    # classificação manual/IA, indexada pelo segundo de início do turno dentro do bloco
    cls = json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))
    t0 = turns[0]["start"]
    keys = {int(k): v for k, v in cls["turnos"].items()}
    faltando = []
    for t in turns:
        rel = t["start"] - t0
        k = min(keys, key=lambda k: abs(k - rel))
        if abs(k - rel) > 1.5:
            faltando.append(f"{rel:.0f}s {t['speaker']}: {t['text'][:60]}")
            continue
        t["tipo"], t["temas"], t["resumo"] = keys.pop(k)
    if faltando or keys:
        raise SystemExit("turnos sem classificação:\n" + "\n".join(faltando) +
                         (f"\nchaves sem turno: {sorted(keys)}" if keys else ""))
    out.update(meta=cls["meta"], afirmacoes=cls["afirmacoes"])

Path(sys.argv[4]).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for who in NOME.values():
    ts = [t for t in turns if t["speaker"] == who]
    print(f"{who}: {len(ts)} turnos, {sum(t['end'] - t['start'] for t in ts) / 60:.1f} min")
