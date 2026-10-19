"""Dá a cada segmento da transcrição o locutor da diarização (L, F ou M), pela maior sobreposição no tempo.

Uso: python rotular.py TRANSCRICAO.json DIAR.json SAIDA.json MAPA
MAPA liga o número do agrupamento de voz ao locutor, ex. "1=L,0=F,2=M". O agrupamento não sabe quem é
quem; o mapa é conferido ouvindo o começo do bloco (a mediação anuncia que Lula abre o confronto).
Usa o rótulo bruto de cada janela (sem a suavização), porque o agente de revisão corrige o resto.
"""
import json
import sys
from collections import Counter
from pathlib import Path

trans = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["segments"]
chunks = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["chunks"]
mapa = {int(k): v for k, v in (p.split("=") for p in sys.argv[4].split(","))}

out = []
for s in trans:
    c = Counter()
    for k in chunks:
        ov = min(s["end"], k["end"]) - max(s["start"], k["start"])
        if ov > 0:
            c[mapa[k["raw"]]] += ov
    out.append(c.most_common(1)[0][0] if c else "?")
Path(sys.argv[3]).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
print(Counter(out))
