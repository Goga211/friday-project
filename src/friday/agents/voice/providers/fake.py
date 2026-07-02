"""Фейковые провайдеры для тестов и офлайн-обкатки пайплайна (без микрофона и облака)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from friday.agents.voice.interfaces import AudioClip


class FakeAudioSource:
    """Отдаёт заранее заданный список кадров и завершается (пайплайн выйдет из run()).

    Перед каждым кадром отдаёт управление циклу событий (как реальный микрофон между
    блоками), чтобы фоновые задачи (воспроизведение ответа) успевали отработать — это
    делает поведение barge-in детерминированным в тестах.
    """

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = frames

    async def frames(self) -> AsyncIterator[bytes]:
        for frame in self._frames:
            await asyncio.sleep(0)
            yield frame


class FakeWakeWord:
    """Срабатывает (score 1.0) на кадрах из trigger_frames, иначе 0.0."""

    def __init__(self, trigger_frames: set[bytes]) -> None:
        self._triggers = trigger_frames
        self.resets = 0

    def process(self, frame: bytes) -> float:
        return 1.0 if frame in self._triggers else 0.0

    def reset(self) -> None:
        self.resets += 1


class FakeRecognizer:
    """Возвращает заданный текст, запоминая, какой PCM ему скормили."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.received: list[bytes] = []

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        self.received.append(pcm)
        return self._text


class FakeSynthesizer:
    """Синтез = текст в байтах (для проверки, что произнесли именно ответ)."""

    def __init__(self, sample_rate: int = 22050) -> None:
        self._sample_rate = sample_rate
        self.spoken: list[str] = []

    async def synthesize(self, text: str) -> AudioClip:
        self.spoken.append(text)
        return AudioClip(pcm=text.encode(), sample_rate=self._sample_rate)


class FakeSink:
    """Запоминает воспроизведённые клипы."""

    def __init__(self) -> None:
        self.played: list[AudioClip] = []
        self.stops = 0

    async def play(self, clip: AudioClip) -> None:
        self.played.append(clip)

    async def stop(self) -> None:
        self.stops += 1
