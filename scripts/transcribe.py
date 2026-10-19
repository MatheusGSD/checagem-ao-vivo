"""Transcreve um trecho do áudio do debate com faster-whisper.

Uso: python transcribe.py AUDIO INICIO_S FIM_S SAIDA.json [modelo] [--vocab] [--hotwords]
--vocab põe os termos de data/vocabulario.json no prompt inicial; --hotwords os passa como hotwords.
Gera JSON com segmentos e palavras, timestamps absolutos (em segundos do arquivo).
"""
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

audio, start, end, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), Path(sys.argv[4])
model_name = sys.argv[5] if len(sys.argv) > 5 and not sys.argv[5].startswith("--") else "large-v3-turbo"

wav = Path(tempfile.gettempdir()) / f"trecho_{int(start)}_{int(end)}.wav"
subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-to", str(end),
     "-i", audio, "-ac", "1", "-ar", "16000", str(wav)],
    check=True,
)

# lê o wav direto: o decode_audio do faster-whisper quebra com a versão do PyAV instalada
with wave.open(str(wav)) as wf:
    sig = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768

prompt = "Debate presidencial na Band, segundo turno. Lula e Flávio Bolsonaro."
hotwords = None
vocab = json.loads((Path(__file__).parent.parent / "data" / "vocabulario.json").read_text(encoding="utf-8"))["termos"]
if "--vocab" in sys.argv:
    # nomes do debate no prompt inicial (com condition_on_previous_text=False, só vale para a 1ª janela)
    prompt += " Citados: " + ", ".join(vocab) + "."
if "--hotwords" in sys.argv:
    # testado e descartado: acerta os nomes em toda janela, mas faz o Whisper pular trechos inteiros
    # (a pergunta de abertura de Lula sumiu); ver avaliar.py
    hotwords = ", ".join(vocab)

model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=8)
t0 = time.time()
segments, info = model.transcribe(
    sig,
    language="pt",
    word_timestamps=True,
    vad_filter=True,
    beam_size=5,
    condition_on_previous_text=False,
    initial_prompt=prompt,
    hotwords=hotwords,
)

result = []
for s in segments:
    result.append({
        "start": round(s.start + start, 2),
        "end": round(s.end + start, 2),
        "text": s.text.strip(),
        "words": [{"w": w.word, "s": round(w.start + start, 2), "e": round(w.end + start, 2),
                   "p": round(w.probability, 3)} for w in (s.words or [])],
    })
    print(f"[{s.start + start:8.1f}] {s.text.strip()[:90]}", flush=True)

out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"audio": audio, "start": start, "end": end, "model": model_name, "prompt": prompt, "hotwords": hotwords,
                           "segments": result}, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"ok: {len(result)} segmentos em {time.time() - t0:.0f}s -> {out}")
