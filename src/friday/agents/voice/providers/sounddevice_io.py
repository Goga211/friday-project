"""Аудио-ввод/вывод через sounddevice (PortAudio): микрофон и колонки.

Wayland не мешает звуку (блокирует только экран/ввод), поэтому захват микрофона работает
на любом DE. Микрофон отдаёт кадры фиксированного размера через фоновый поток PortAudio →
очередь → async-генератор. Вывод воспроизводит клип целиком; stop() прерывает (barge-in).
"""

from __future__ import annotations

import asyncio
import logging
import queue
from collections.abc import AsyncIterator

from friday.agents.voice.config import VoiceSettings
from friday.agents.voice.interfaces import AudioClip

log = logging.getLogger("friday.voice.audio")


def _device(value: str | None) -> str | int | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else value


class MicSource:
    def __init__(self, settings: VoiceSettings) -> None:
        self._sample_rate = settings.sample_rate
        self._frame_samples = settings.frame_samples
        self._device = _device(settings.input_device)

    async def frames(self) -> AsyncIterator[bytes]:
        import sounddevice as sd

        frames: queue.Queue[bytes] = queue.Queue()

        def callback(indata: bytes, _frames: int, _time: object, status: object) -> None:
            if status:
                log.warning("статус захвата аудио: %s", status)
            frames.put(bytes(indata))

        stream = sd.RawInputStream(
            samplerate=self._sample_rate,
            blocksize=self._frame_samples,
            dtype="int16",
            channels=1,
            device=self._device,
            callback=callback,
        )
        stream.start()
        log.info("микрофон открыт (%d Гц, кадр %d сэмплов)", self._sample_rate, self._frame_samples)
        try:
            while True:
                yield await asyncio.to_thread(frames.get)
        finally:
            stream.stop()
            stream.close()


class SpeakerSink:
    def __init__(self, settings: VoiceSettings) -> None:
        self._device = _device(settings.output_device)
        self._gain = settings.tts_volume

    async def play(self, clip: AudioClip) -> None:
        import numpy as np
        import sounddevice as sd

        samples = np.frombuffer(clip.pcm, dtype=np.int16)
        if self._gain != 1.0:
            # тише и без клиппинга: масштабируем во float, обрезаем по int16, обратно в int16
            scaled = samples.astype(np.float32) * self._gain
            samples = np.clip(scaled, -32768.0, 32767.0).astype(np.int16)

        def _blocking() -> None:
            sd.play(samples, clip.sample_rate, device=self._device)
            sd.wait()

        await asyncio.to_thread(_blocking)

    async def stop(self) -> None:
        import sounddevice as sd

        await asyncio.to_thread(sd.stop)
