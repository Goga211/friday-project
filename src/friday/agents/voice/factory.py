"""Фабрика провайдеров: собирает реальные адаптеры или фейки по настройкам.

Реальные адаптеры импортируются лениво (внутри веток), чтобы пайплайн и тесты не тянули
тяжёлые опциональные зависимости (openwakeword/sounddevice/numpy). Если зависимость не
установлена — понятная ошибка с подсказкой поставить extra `voice`.
"""

from __future__ import annotations

from friday.agents.voice.config import VoiceSettings
from friday.agents.voice.interfaces import (
    AudioSink,
    AudioSource,
    SpeechRecognizer,
    SpeechSynthesizer,
    WakeWordDetector,
)
from friday.agents.voice.providers.fake import (
    FakeAudioSource,
    FakeRecognizer,
    FakeSink,
    FakeSynthesizer,
    FakeWakeWord,
)


def build_source(settings: VoiceSettings) -> AudioSource:
    if settings.audio == "fake":
        return FakeAudioSource([])
    if settings.audio == "sounddevice":
        from friday.agents.voice.providers.sounddevice_io import MicSource

        return MicSource(settings)
    raise ValueError(f"неизвестный источник аудио: {settings.audio}")


def build_sink(settings: VoiceSettings) -> AudioSink:
    if settings.audio == "fake":
        return FakeSink()
    if settings.audio == "sounddevice":
        from friday.agents.voice.providers.sounddevice_io import SpeakerSink

        return SpeakerSink(settings)
    raise ValueError(f"неизвестный вывод аудио: {settings.audio}")


def build_wake(settings: VoiceSettings) -> WakeWordDetector:
    if settings.wake == "fake":
        return FakeWakeWord(set())
    if settings.wake == "pushtotalk":
        from friday.agents.voice.providers.pushtotalk import PushToTalkWakeWord

        return PushToTalkWakeWord()
    if settings.wake == "openwakeword":
        from friday.agents.voice.providers.openwakeword_ww import OpenWakeWordDetector

        return OpenWakeWordDetector(settings)
    raise ValueError(f"неизвестный детектор wake-word: {settings.wake}")


def build_recognizer(settings: VoiceSettings) -> SpeechRecognizer:
    if settings.stt == "fake":
        return FakeRecognizer("")
    if settings.stt == "yandex":
        from friday.agents.voice.providers.yandex_stt import YandexSpeechRecognizer

        return YandexSpeechRecognizer(settings)
    raise ValueError(f"неизвестный STT-провайдер: {settings.stt}")


def build_synthesizer(settings: VoiceSettings) -> SpeechSynthesizer:
    if settings.tts == "fake":
        return FakeSynthesizer()
    if settings.tts == "piper":
        from friday.agents.voice.providers.piper_tts import PiperSpeechSynthesizer

        return PiperSpeechSynthesizer(settings)
    if settings.tts == "yandex":
        from friday.agents.voice.providers.yandex_tts import YandexSpeechSynthesizer

        return YandexSpeechSynthesizer(settings)
    raise ValueError(f"неизвестный TTS-провайдер: {settings.tts}")
