"""STT через Yandex SpeechKit (синхронное распознавание короткого аудио, API v1).

POST на stt:recognize сырым LPCM (16-бит моно). Лимиты сервиса: ≤ 1 МБ / ≤ 30 с — фразы
после wake-word в них укладываются (потолок записи задаёт VoiceSettings.max_phrase_seconds).
Реализация за интерфейсом SpeechRecognizer, поэтому провайдер меняется в конфиге (Whisper и т.п.).
"""

from __future__ import annotations

import logging

import httpx

from friday.agents.voice.config import VoiceSettings

log = logging.getLogger("friday.voice.stt")

_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
_MAX_BYTES = 1024 * 1024  # лимит Yandex sync — 1 МБ


class YandexSpeechRecognizer:
    def __init__(self, settings: VoiceSettings) -> None:
        if not settings.yandex_api_key:
            raise RuntimeError("FRIDAY_VOICE_YANDEX_API_KEY не задан")
        self._api_key = settings.yandex_api_key
        self._folder_id = settings.yandex_folder_id
        self._lang = settings.yandex_lang

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        if len(pcm) > _MAX_BYTES:
            log.warning("аудио %d байт превышает лимит Yandex 1 МБ — обрезаю", len(pcm))
            pcm = pcm[:_MAX_BYTES]

        params = {
            "lang": self._lang,
            "format": "lpcm",
            "sampleRateHertz": str(sample_rate),
        }
        if self._folder_id:
            params["folderId"] = self._folder_id
        headers = {"Authorization": f"Api-Key {self._api_key}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(_STT_URL, params=params, headers=headers, content=pcm)
            response.raise_for_status()
            data = response.json()
        return str(data.get("result", ""))
