"""TTS через Yandex SpeechKit v3 (пакет yandex-speechkit, gRPC).

Голоса v3 современнее v1 REST (dasha, marina, alexander… + премиум alena/jane с ролями).
Пакет синхронный (gRPC под капотом) и отдаёт pydub AudioSegment — синтез гоняем в потоке
(asyncio.to_thread), приводим к 48 кГц/16-бит/моно и отдаём сырой PCM для AudioSink. Ключ и
folder — те же, что у STT. За интерфейсом SpeechSynthesizer, выбор в конфиге (tts=yandex_v3).
"""

from __future__ import annotations

import asyncio
import logging

from friday.agents.voice.config import VoiceSettings
from friday.agents.voice.interfaces import AudioClip

log = logging.getLogger("friday.voice.tts")


class YandexV3SpeechSynthesizer:
    def __init__(self, settings: VoiceSettings) -> None:
        if not settings.yandex_api_key:
            raise RuntimeError("FRIDAY_VOICE_YANDEX_API_KEY не задан")
        from speechkit import configure_credentials, creds

        configure_credentials(
            yandex_credentials=creds.YandexCredentials(api_key=settings.yandex_api_key)
        )
        self._voice = settings.yandex_tts_voice
        self._role = settings.yandex_tts_emotion
        self._sample_rate = settings.yandex_tts_sample_rate

    async def synthesize(self, text: str) -> AudioClip:
        # Синтез синхронный (gRPC) — уводим в поток, чтобы не блокировать event loop.
        return await asyncio.to_thread(self._synthesize_blocking, text)

    def _synthesize_blocking(self, text: str) -> AudioClip:
        from speechkit import model_repository

        model = model_repository.synthesis_model()
        model.voice = self._voice
        if self._role and self._role != "neutral":  # роль/эмоция поддерживается не всеми голосами
            model.role = self._role
        result = model.synthesize(text, raw_format=False)
        seg = result.set_frame_rate(self._sample_rate).set_channels(1).set_sample_width(2)
        return AudioClip(pcm=seg.raw_data, sample_rate=self._sample_rate)
