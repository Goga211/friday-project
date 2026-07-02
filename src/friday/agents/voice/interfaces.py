"""Контракты голосового пайплайна: аудио-ввод/вывод, wake-word, STT, TTS.

Всё, что зависит от железа или облака, спрятано за этими Protocol'ами. Пайплайн работает
только с ними, поэтому подменяется фейками в тестах. Формат аудио на входе — 16-бит PCM,
little-endian, моно (кадры кратны 80 мс для openWakeWord); это же требует Yandex LPCM.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AudioClip:
    """Готовый к воспроизведению фрагмент: сырой 16-бит PCM моно + частота дискретизации."""

    pcm: bytes
    sample_rate: int


class AudioSource(Protocol):
    """Источник звука: асинхронный поток кадров PCM (обычно с микрофона)."""

    def frames(self) -> AsyncIterator[bytes]: ...


class AudioSink(Protocol):
    """Вывод звука: воспроизводит клип; stop() прерывает (для barge-in)."""

    async def play(self, clip: AudioClip) -> None: ...

    async def stop(self) -> None: ...


class WakeWordDetector(Protocol):
    """Детектор wake-word: на кадр возвращает уверенность 0..1; reset — сброс состояния."""

    def process(self, frame: bytes) -> float: ...

    def reset(self) -> None: ...


class SpeechRecognizer(Protocol):
    """STT: превращает записанный PCM в текст (обычно облачный вызов)."""

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str: ...


class SpeechSynthesizer(Protocol):
    """TTS: превращает текст в аудио-клип для воспроизведения."""

    async def synthesize(self, text: str) -> AudioClip: ...
