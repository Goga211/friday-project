"""Настройки голосового агента из окружения/.env (префикс CHRISTOPHER_VOICE_)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class VoiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHRISTOPHER_VOICE_",
        env_file=".env",
        extra="ignore",
    )

    # --- Формат аудио (16 кГц/16-бит/моно — требование openWakeWord и Yandex LPCM) ---
    sample_rate: int = 16000
    frame_samples: int = 1280  # 80 мс при 16 кГц — минимальный кадр openWakeWord

    # --- Выбор реализаций провайдеров (real | fake) ---
    audio: str = "sounddevice"  # источник/вывод звука
    wake: str = "openwakeword"  # детектор wake-word
    stt: str = "yandex"  # распознавание речи
    tts: str = "piper"  # синтез речи

    # --- Wake-word ---
    # Путь к обученной модели «Кристофер» (.onnx/.tflite). Пусто → предобученные (НЕ «Кристофер»).
    wake_model: str | None = None
    wake_threshold: float = 0.5
    # Перебивать ли ответ TTS новым wake-word (barge-in). Выключи, если ложные срабатывания.
    barge_in: bool = True

    # --- Конец фразы (VAD по энергии) ---
    vad_energy_threshold: float = 400.0  # RMS int16, выше — речь
    silence_seconds: float = 0.8  # столько тишины после речи = конец фразы
    min_phrase_seconds: float = 0.3  # короче — считаем ложным срабатыванием
    max_phrase_seconds: float = 15.0  # жёсткий потолок (Yandex sync ≤ 30 с / 1 МБ)

    # --- Yandex SpeechKit (STT) ---
    yandex_api_key: str | None = None
    yandex_folder_id: str | None = None
    yandex_lang: str = "ru-RU"

    # --- Piper (TTS) ---
    piper_bin: str = "piper"
    piper_model: str | None = None  # путь к .onnx голосу Piper (русский)
    piper_sample_rate: int = 22050  # частота голоса Piper (обычно 22050)

    # --- Аудио-устройства (None → системные по умолчанию) ---
    input_device: str | None = None
    output_device: str | None = None

    # --- Таймаут ожидания ответа мозга на фразу, сек ---
    reply_timeout: float = 60.0
