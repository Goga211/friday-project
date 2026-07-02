"""TTS через Yandex SpeechKit (нейросетевые голоса, API v1).

POST на tts:synthesize с form-параметрами; в ответ — сырой LPCM 16-бит моно на запрошенной
частоте (для lpcm валидны 8000/16000/48000 Гц — берём 48000 ради качества). Голос/эмоция/темп
задаются конфигом. За интерфейсом SpeechSynthesizer, поэтому меняется в конфиге (piper|yandex).
Ключ и folderId — те же, что у STT (yandex_api_key / yandex_folder_id).
"""

from __future__ import annotations

import logging

import httpx

from christopher.agents.voice.config import VoiceSettings
from christopher.agents.voice.interfaces import AudioClip

log = logging.getLogger("christopher.voice.tts")

_TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"


class YandexSpeechSynthesizer:
    def __init__(self, settings: VoiceSettings) -> None:
        if not settings.yandex_api_key:
            raise RuntimeError("CHRISTOPHER_VOICE_YANDEX_API_KEY не задан")
        self._api_key = settings.yandex_api_key
        self._folder_id = settings.yandex_folder_id
        self._lang = settings.yandex_lang
        self._voice = settings.yandex_tts_voice
        self._emotion = settings.yandex_tts_emotion
        self._speed = settings.yandex_tts_speed
        self._sample_rate = settings.yandex_tts_sample_rate

    async def synthesize(self, text: str) -> AudioClip:
        data = {
            "text": text,
            "lang": self._lang,
            "voice": self._voice,
            "emotion": self._emotion,
            "speed": str(self._speed),
            "format": "lpcm",
            "sampleRateHertz": str(self._sample_rate),
        }
        if self._folder_id:
            data["folderId"] = self._folder_id
        headers = {"Authorization": f"Api-Key {self._api_key}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(_TTS_URL, data=data, headers=headers)
            response.raise_for_status()
            pcm = response.content
        return AudioClip(pcm=pcm, sample_rate=self._sample_rate)
