"""Detecção de voz Silero, copiada de C:\\dev\\wisprflow-ptbr\\src\\wisprflow\\vad.py (Chunker, SegmentAssembler,
SileroVad), sem depender daquele pacote. Mudanças: taxa fixa em 16 kHz e a propriedade
`SegmentAssembler.amostras`, que dá ao chamador o tamanho do segmento em andamento para ele calcular
o início do segmento contando amostras (o original não devolve tempos).
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

TAXA = 16000
CHUNK = 512  # o Silero v5/v6 exige blocos de exatamente 512 amostras a 16 kHz (32 ms)

DEFAULT_THRESHOLD = 0.5
DEFAULT_MIN_SILENCE_MS = 700
DEFAULT_MIN_SPEECH_MS = 250
DEFAULT_SPEECH_PAD_MS = 200
MAX_SEGMENT_S = 60.0


class Chunker:
    """Re-blocka um fluxo de arrays em blocos exatos de `chunk` amostras."""

    def __init__(self, chunk: int = CHUNK):
        self._chunk = chunk
        self._rest = np.empty(0, dtype=np.float32)

    def push(self, arr: np.ndarray) -> list[np.ndarray]:
        data = np.concatenate([self._rest, np.asarray(arr, dtype=np.float32).reshape(-1)])
        n = self._chunk
        out = [data[i: i + n] for i in range(0, len(data) - n + 1, n)]
        self._rest = data[len(out) * n:]
        return out

    def flush(self) -> np.ndarray | None:
        rest, self._rest = self._rest, np.empty(0, dtype=np.float32)
        return rest if len(rest) else None


class SegmentAssembler:
    """Máquina de estados que monta segmentos de fala a partir de (chunk, prob).

    `feed` devolve ("start", None) na primeira prob >= threshold (o segmento nasce com o pré-rolo de
    speech_pad_ms), ("segment", audio) quando fecha (min_silence_ms de silêncio, com o rabo aparado até
    o pad, ou max_segment_s; fala < min_speech_ms é descartada) ou ("none", None).
    """

    def __init__(self, *, threshold=DEFAULT_THRESHOLD, min_silence_ms=DEFAULT_MIN_SILENCE_MS,
                 min_speech_ms=DEFAULT_MIN_SPEECH_MS, speech_pad_ms=DEFAULT_SPEECH_PAD_MS,
                 max_segment_s=MAX_SEGMENT_S, sample_rate=TAXA, chunk=CHUNK):
        self._threshold = threshold
        chunk_ms = chunk / sample_rate * 1000.0
        self._pad_chunks = max(1, math.ceil(speech_pad_ms / chunk_ms))
        self._silence_chunks = max(1, math.ceil(min_silence_ms / chunk_ms))
        self._min_speech_samples = int(min_speech_ms / 1000.0 * sample_rate)
        self._max_samples = int(max_segment_s * sample_rate)
        self._ring: deque = deque(maxlen=self._pad_chunks)
        self._reset_segment()

    def _reset_segment(self):
        self._segment: list[np.ndarray] = []
        self._segment_samples = 0
        self._speech_samples = 0
        self._silence_run = 0
        self._active = False

    @property
    def speech_active(self) -> bool:
        return self._active

    @property
    def amostras(self) -> int:
        """Amostras do segmento em andamento, com o pré-rolo (0 fora de um segmento)."""
        return self._segment_samples if self._active else 0

    def feed(self, chunk: np.ndarray, prob: float):
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if not self._active:
            if prob >= self._threshold:
                self._segment = list(self._ring) + [chunk]
                self._segment_samples = sum(len(c) for c in self._segment)
                self._speech_samples = len(chunk)
                self._silence_run = 0
                self._active = True
                self._ring.clear()
                return "start", None
            self._ring.append(chunk)
            return "none", None

        self._segment.append(chunk)
        self._segment_samples += len(chunk)
        if prob >= self._threshold:
            self._silence_run = 0
            self._speech_samples += len(chunk)
        else:
            self._silence_run += 1

        if self._silence_run >= self._silence_chunks:
            return self._close(trim_tail=True)
        if self._segment_samples >= self._max_samples:
            return self._close(trim_tail=False)
        return "none", None

    def _close(self, *, trim_tail: bool):
        segment = self._segment
        if trim_tail and self._silence_run > self._pad_chunks:
            segment = segment[: len(segment) - (self._silence_run - self._pad_chunks)]
        speech_ok = self._speech_samples >= self._min_speech_samples
        self._reset_segment()
        if not speech_ok:
            return "none", None
        return "segment", np.concatenate(segment)

    def flush(self) -> np.ndarray | None:
        """Segmento em andamento (fim da fonte), se tiver fala suficiente. Sempre reseta o estado."""
        if not self._active or not self._segment:
            self._reset_segment()
            return None
        segment, speech_ok = self._segment, self._speech_samples >= self._min_speech_samples
        self._reset_segment()
        return np.concatenate(segment) if speech_ok else None


class SileroVad:
    """Modelo Silero (JIT embutido no wheel `silero_vad`, offline), na CPU."""

    def __init__(self):
        self._model = None

    def load(self):
        if self._model is None:
            from silero_vad import load_silero_vad

            self._model = load_silero_vad()

    def prob(self, chunk: np.ndarray) -> float:
        import torch

        return float(self._model(torch.from_numpy(np.ascontiguousarray(chunk, dtype=np.float32)), TAXA).item())

    def reset(self):
        if self._model is not None:
            self._model.reset_states()
