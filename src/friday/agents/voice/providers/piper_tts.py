"""TTS через Piper (локально, лёгкий). Зовём бинарь и читаем сырой PCM из stdout.

Подключаемся к бинарю (`piper --model <voice.onnx> --output_raw`), а не к python-пакету:
CLI стабилен между версиями и не тянет тяжёлые зависимости в процесс агента. Голос (русский
.onnx) и его частоту задаёт конфиг. Выход — сырой 16-бит PCM моно для AudioSink.
"""

from __future__ import annotations

import asyncio
import logging

from friday.agents.voice.config import VoiceSettings
from friday.agents.voice.interfaces import AudioClip

log = logging.getLogger("friday.voice.tts")


class PiperSpeechSynthesizer:
    def __init__(self, settings: VoiceSettings) -> None:
        if not settings.piper_model:
            raise RuntimeError("FRIDAY_VOICE_PIPER_MODEL не задан (путь к голосу .onnx)")
        self._bin = settings.piper_bin
        self._model = settings.piper_model
        self._sample_rate = settings.piper_sample_rate

    async def synthesize(self, text: str) -> AudioClip:
        proc = await asyncio.create_subprocess_exec(
            self._bin,
            "--model",
            self._model,
            "--output_raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(text.encode())
        if proc.returncode != 0:
            detail = stderr.decode()[:200]
            raise RuntimeError(f"piper завершился с кодом {proc.returncode}: {detail}")
        return AudioClip(pcm=stdout, sample_rate=self._sample_rate)
