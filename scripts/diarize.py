"""Separa locutores: embedding ECAPA por janela de fala + clusterização.

Uso: python diarize.py AUDIO TRANSCRICAO.json SAIDA.json [n_locutores]
Quebra cada segmento do whisper em janelas de ~3 s (pelas palavras), extrai o
embedding de voz de cada janela, agrupa em n clusters e suaviza os rótulos
(no relógio de xadrez os turnos são longos, trocas de 1 janela são ruído).
"""
import json
import subprocess
import sys
import tempfile
import wave
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from sklearn.cluster import AgglomerativeClustering
from speechbrain.inference.speaker import EncoderClassifier
from speechbrain.utils.fetching import LocalStrategy

audio, trans_path, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
n_spk = int(sys.argv[4]) if len(sys.argv) > 4 else 3
trans = json.loads(trans_path.read_text(encoding="utf-8"))
base = trans["start"]

wav_path = Path(tempfile.gettempdir()) / f"diar_{int(base)}.wav"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(base), "-to", str(trans["end"]),
                "-i", audio, "-ac", "1", "-ar", "16000", str(wav_path)], check=True)
with wave.open(str(wav_path)) as wf:
    sig = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768

# janelas de ~3 s agrupando palavras
chunks = []
for seg in trans["segments"]:
    words = seg["words"] or [{"w": seg["text"], "s": seg["start"], "e": seg["end"]}]
    cur = []
    for w in words:
        cur.append(w)
        if cur[-1]["e"] - cur[0]["s"] >= 3.0:
            chunks.append(cur)
            cur = []
    if cur:
        if chunks and cur[-1]["e"] - cur[0]["s"] < 1.0 and chunks[-1][-1]["e"] >= cur[0]["s"] - 0.5:
            chunks[-1].extend(cur)  # resto curto demais: junta no anterior
        else:
            chunks.append(cur)

enc = EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    savedir=str(Path(tempfile.gettempdir()) / "ecapa"),
    local_strategy=LocalStrategy.COPY,
)

embs, keep = [], []
for i, c in enumerate(chunks):
    a, b = int((c[0]["s"] - base) * 16000), int((c[-1]["e"] - base) * 16000)
    if b - a < 8000:  # < 0,5 s não dá embedding confiável
        continue
    with torch.no_grad():
        e = enc.encode_batch(torch.from_numpy(sig[a:b]).unsqueeze(0)).squeeze().cpu().numpy()
    embs.append(e / np.linalg.norm(e))
    keep.append(i)

X = np.stack(embs)
labels = AgglomerativeClustering(n_clusters=n_spk, metric="cosine", linkage="average").fit_predict(X)

# suavização: moda numa janela de 5
smooth = labels.copy()
for i in range(len(labels)):
    win = labels[max(0, i - 2): i + 3]
    smooth[i] = Counter(win).most_common(1)[0][0]

rows = []
for idx, lab, raw in zip(keep, smooth, labels):
    c = chunks[idx]
    rows.append({"start": c[0]["s"], "end": c[-1]["e"], "spk": int(lab), "raw": int(raw),
                 "text": "".join(w["w"] for w in c).strip()})

# junta janelas consecutivas do mesmo locutor em turnos
turns = []
for r in rows:
    if turns and turns[-1]["spk"] == r["spk"] and r["start"] - turns[-1]["end"] < 4:
        turns[-1]["end"] = r["end"]
        turns[-1]["text"] += " " + r["text"]
    else:
        turns.append(dict(r))

out.write_text(json.dumps({"n_spk": n_spk, "chunks": rows, "turns": turns}, ensure_ascii=False, indent=1),
               encoding="utf-8")
for s in range(n_spk):
    tot = sum(t["end"] - t["start"] for t in turns if t["spk"] == s)
    print(f"locutor {s}: {tot / 60:.1f} min em {sum(t['spk'] == s for t in turns)} turnos")
print(f"ok -> {out}")
