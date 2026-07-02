"""Голосовой пайплайн: конечный автомат wake → запись → STT → мозг → TTS → barge-in.

Слушает поток кадров источника. В состоянии WAIT ждёт wake-word; поймав — переходит в
RECORD, копит кадры до конца фразы (эндпоинтер), затем распознаёт, отдаёт текст мозгу
(колбэк on_transcript) и произносит ответ. Ответ проигрывается фоновой задачей, а цикл
продолжает слушать: если во время речи снова прозвучал wake-word — воспроизведение
прерывается (barge-in) и начинается запись новой фразы. Все зависимости внедряются,
поэтому весь цикл проверяется на фейках. run() завершается, когда источник иссяк
(в проде — бесконечен).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from enum import Enum, auto

from friday.agents.voice.config import VoiceSettings
from friday.agents.voice.interfaces import (
    AudioSink,
    AudioSource,
    SpeechRecognizer,
    SpeechSynthesizer,
    WakeWordDetector,
)
from friday.agents.voice.vad import Endpointer

log = logging.getLogger("friday.voice")

# on_transcript: распознанный текст → текст ответа ассистента (обычно раунд через мозг).
TranscriptHandler = Callable[[str], Awaitable[str]]
# on_event: (kind, text), kind in {"transcript", "say"} — для HUD/наблюдаемости.
EventHandler = Callable[[str, str], Awaitable[None]]


class _State(Enum):
    WAIT = auto()  # ждём wake-word
    RECORD = auto()  # пишем фразу до конца
    SPEAK = auto()  # произносим ответ, слушаем barge-in


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
        self._barge_in = settings.barge_in
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
        speaking: asyncio.Task[None] | None = None
        log.info("голосовой пайплайн запущен, слушаю wake-word")

        async for frame in self._source.frames():
            if state is _State.SPEAK:
                assert speaking is not None
                # barge-in: пользователь перебил новым wake-word во время ответа.
                if self._barge_in and self._wake.process(frame) >= self._settings.wake_threshold:
                    log.info("wake-word во время ответа — прерываю (barge-in), пишу новую фразу")
                    await self._cancel_speech(speaking)
                    speaking = None
                    self._endpointer.reset()
                    buffer.clear()
                    state = _State.RECORD
                    continue
                if speaking.done():
                    await self._finish_speech(speaking)
                    speaking = None
                    self._wake.reset()
                    state = _State.WAIT
                continue

            if state is _State.WAIT:
                if self._wake.process(frame) >= self._settings.wake_threshold:
                    log.info("wake-word обнаружен — пишу фразу")
                    self._endpointer.reset()
                    buffer.clear()
                    state = _State.RECORD
                continue

            # RECORD
            buffer.extend(frame)
            if self._endpointer.feed(frame):
                reply = await self._handle_phrase(bytes(buffer))
                buffer.clear()
                if reply:
                    self._wake.reset()  # чистим детектор перед прослушкой barge-in
                    speaking = asyncio.create_task(self._speak(reply))
                    state = _State.SPEAK
                else:
                    self._wake.reset()
                    state = _State.WAIT

        if speaking is not None:  # источник иссяк во время ответа — дослушиваем воспроизведение
            await self._finish_speech(speaking)
            self._wake.reset()

    async def _handle_phrase(self, pcm: bytes) -> str | None:
        """Распознать фразу и получить ответ мозга. None — если распознать/ответить не вышло."""
        try:
            text = (await self._recognizer.transcribe(pcm, self._settings.sample_rate)).strip()
        except Exception:  # noqa: BLE001 — сбой STT не должен ронять пайплайн
            log.exception("ошибка распознавания речи")
            return None
        if not text:
            log.info("STT вернул пустую строку — пропускаю")
            return None

        log.info("распознано: %s", text)
        await self._emit("transcript", text)

        try:
            reply = await self._on_transcript(text)
        except Exception:  # noqa: BLE001 — сбой мозга/связи не должен ронять пайплайн
            log.exception("ошибка получения ответа мозга")
            return None
        return reply if reply.strip() else None

    async def _speak(self, text: str) -> None:
        await self._emit("say", text)
        try:
            clip = await self._synth.synthesize(text)
            await self._sink.play(clip)
        except asyncio.CancelledError:  # barge-in прервал воспроизведение — это штатно
            raise
        except Exception:  # noqa: BLE001 — сбой TTS/вывода не должен ронять пайплайн
            log.exception("ошибка синтеза/воспроизведения")

    async def _cancel_speech(self, task: asyncio.Task[None]) -> None:
        """Прервать воспроизведение ответа (barge-in): остановить вывод и снять задачу."""
        with contextlib.suppress(Exception):
            await self._sink.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @staticmethod
    async def _finish_speech(task: asyncio.Task[None]) -> None:
        """Дождаться штатного завершения воспроизведения, поглотив возможную ошибку."""
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _emit(self, kind: str, text: str) -> None:
        if self._on_event is None:
            return
        try:
            await self._on_event(kind, text)
        except Exception:  # noqa: BLE001 — наблюдаемость не критична
            log.exception("ошибка публикации voice-события %s", kind)
