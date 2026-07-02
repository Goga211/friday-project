"""Определение конца фразы по тишине (простой энергетический VAD).

Не полноценный VAD, а эндпоинтер: после wake-word мы пишем звук и хотим понять, когда
пользователь договорил. Логика — по RMS-энергии кадра: копим речь, а как только набралось
достаточно тишины после речи — фраза закончена. Потолок по длительности защищает от
бесконечной записи. Класс чистый (без I/O) — легко тестируется покадрово.
"""

from __future__ import annotations

import array


def frame_rms(frame: bytes) -> float:
    """RMS-энергия кадра 16-бит PCM (int16, little-endian). Без numpy — на stdlib."""
    if len(frame) < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(frame[: len(frame) - (len(frame) % 2)])
    if not samples:
        return 0.0
    total = sum(sample * sample for sample in samples)
    return float((total / len(samples)) ** 0.5)


class Endpointer:
    """Копит кадры фразы и сообщает, когда пользователь договорил."""

    def __init__(
        self,
        *,
        sample_rate: int,
        frame_samples: int,
        energy_threshold: float,
        silence_seconds: float,
        min_phrase_seconds: float,
        max_phrase_seconds: float,
    ) -> None:
        self._frame_seconds = frame_samples / sample_rate
        self._energy_threshold = energy_threshold
        self._silence_seconds = silence_seconds
        self._min_phrase_seconds = min_phrase_seconds
        self._max_phrase_seconds = max_phrase_seconds
        self.reset()

    def reset(self) -> None:
        self._elapsed = 0.0
        self._trailing_silence = 0.0
        self._speech_seen = False

    def feed(self, frame: bytes) -> bool:
        """Скормить кадр записи. True — фраза закончена (пора распознавать)."""
        self._elapsed += self._frame_seconds
        if frame_rms(frame) >= self._energy_threshold:
            self._speech_seen = True
            self._trailing_silence = 0.0
        else:
            self._trailing_silence += self._frame_seconds

        if self._elapsed >= self._max_phrase_seconds:
            return True
        if not self._speech_seen or self._elapsed < self._min_phrase_seconds:
            return False
        return self._trailing_silence >= self._silence_seconds
