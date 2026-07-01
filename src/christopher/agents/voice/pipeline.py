"""Голосовой пайплайн: конечный автомат wake → запись → STT → мозг → TTS.

Слушает поток кадров источника. В состоянии WAIT ждёт wake-word; поймав — переходит в
RECORD, копит кадры до конца фразы (эндпоинтер), затем распознаёт, отдаёт текст мозгу
(колбэк on_transcript) и произносит ответ. Все зависимости внедряются, поэтому весь цикл
проверяется на фейках. run() завершается, когда источник иссяк (в проде — бесконечен).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from enum import Enum, auto

from christopher.agents.voice.config import VoiceSettings
from christopher.agents.voice.interfaces import (
    AudioSink,
    AudioSource,
    SpeechRecognizer,
    SpeechSynthesizer,
    WakeWordDetector,
)
from christopher.agents.voice.vad import Endpointer

log = logging.getLogger("christopher.voice")

# on_transcript: распознанный текст → текст ответа ассистента (обычно раунд через мозг).
TranscriptHandler = Callable[[str], Awaitable[str]]
# on_event: (kind, text), kind in {"transcript", "say"} — для HUD/наблюдаемости.
EventHandler = Callable[[str, str], Awaitable[None]]


class _State(Enum):
    WAIT = auto()  # ждём wake-word
    RECORD = auto()  # пишем фразу до конца


class VoicePipeline:
    def __init__(
        self,
        *,
        source: AudioSource,
        wake: WakeWordDetector,
        recognizer: SpeechRecognizer,
        synthesizer: SpeechSynthesizer,
        sink: AudioSink,
        settings: VoiceSettings,
        on_transcript: TranscriptHandler,
        on_event: EventHandler | None = None,
    ) -> None:
        self._source = source
        self._wake = wake
        self._recognizer = recognizer
        self._synth = synthesizer
        self._sink = sink
        self._settings = settings
        self._on_transcript = on_transcript
        self._on_event = on_event
        self._endpointer = Endpointer(
            sample_rate=settings.sample_rate,
            frame_samples=settings.frame_samples,
            energy_threshold=settings.vad_energy_threshold,
            silence_seconds=settings.silence_seconds,
            min_phrase_seconds=settings.min_phrase_seconds,
            max_phrase_seconds=settings.max_phrase_seconds,
        )

    async def run(self) -> None:
        state = _State.WAIT
        buffer = bytearray()
        log.info("голосовой пайплайн запущен, слушаю wake-word")

        async for frame in self._source.frames():
            if state is _State.WAIT:
                if self._wake.process(frame) >= self._settings.wake_threshold:
                    log.info("wake-word обнаружен — пишу фразу")
                    self._endpointer.reset()
                    buffer.clear()
                    state = _State.RECORD
                continue

            buffer.extend(frame)
            if self._endpointer.feed(frame):
                await self._handle_phrase(bytes(buffer))
                self._wake.reset()
                buffer.clear()
                state = _State.WAIT

    async def _handle_phrase(self, pcm: bytes) -> None:
        try:
            text = (await self._recognizer.transcribe(pcm, self._settings.sample_rate)).strip()
        except Exception:  # noqa: BLE001 — сбой STT не должен ронять пайплайн
            log.exception("ошибка распознавания речи")
            return
        if not text:
            log.info("STT вернул пустую строку — пропускаю")
            return

        log.info("распознано: %s", text)
        await self._emit("transcript", text)

        try:
            reply = await self._on_transcript(text)
        except Exception:  # noqa: BLE001 — сбой мозга/связи не должен ронять пайплайн
            log.exception("ошибка получения ответа мозга")
            return

        await self._speak(reply)

    async def _speak(self, text: str) -> None:
        if not text.strip():
            return
        await self._emit("say", text)
        try:
            clip = await self._synth.synthesize(text)
            await self._sink.play(clip)
        except Exception:  # noqa: BLE001 — сбой TTS/вывода не должен ронять пайплайн
            log.exception("ошибка синтеза/воспроизведения")

    async def _emit(self, kind: str, text: str) -> None:
        if self._on_event is None:
            return
        try:
            await self._on_event(kind, text)
        except Exception:  # noqa: BLE001 — наблюдаемость не критична
            log.exception("ошибка публикации voice-события %s", kind)
