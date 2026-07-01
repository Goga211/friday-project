"""Юнит-тесты голосового пайплайна на фейках (без микрофона и облака)."""

from __future__ import annotations

import array

import pytest

from christopher.agents.voice.config import VoiceSettings
from christopher.agents.voice.factory import build_recognizer, build_sink, build_source, build_wake
from christopher.agents.voice.pipeline import VoicePipeline
from christopher.agents.voice.providers.fake import (
    FakeAudioSource,
    FakeRecognizer,
    FakeSink,
    FakeSynthesizer,
    FakeWakeWord,
)

FRAME_SAMPLES = 1280


def _frame(value: int, n: int = FRAME_SAMPLES) -> bytes:
    return array.array("h", [value] * n).tobytes()


SILENCE = _frame(0)
SPEECH = _frame(5000)
WAKE = _frame(2)  # уникальный маркер, на который срабатывает FakeWakeWord


def _settings(**overrides: object) -> VoiceSettings:
    base: dict[str, object] = {
        "audio": "fake",
        "wake": "fake",
        "stt": "fake",
        "tts": "fake",
        "vad_energy_threshold": 400.0,
        "silence_seconds": 0.16,
        "min_phrase_seconds": 0.16,
        "max_phrase_seconds": 5.0,
    }
    base.update(overrides)
    return VoiceSettings(_env_file=None, **base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_full_cycle_wake_record_stt_reply_say() -> None:
    frames = [SILENCE, WAKE, SPEECH, SPEECH, SPEECH, SPEECH, SILENCE, SILENCE]
    source = FakeAudioSource(frames)
    wake = FakeWakeWord({WAKE})
    recognizer = FakeRecognizer("какая погода")
    synth = FakeSynthesizer()
    sink = FakeSink()

    heard: list[str] = []
    events: list[tuple[str, str]] = []

    async def on_transcript(text: str) -> str:
        heard.append(text)
        return "погода ясная"

    async def on_event(kind: str, text: str) -> None:
        events.append((kind, text))

    pipeline = VoicePipeline(
        source=source,
        wake=wake,
        recognizer=recognizer,
        synthesizer=synth,
        sink=sink,
        settings=_settings(),
        on_transcript=on_transcript,
        on_event=on_event,
    )
    await pipeline.run()

    assert heard == ["какая погода"]
    # записана вся фраза после wake-word (кадр wake в буфер не попадает)
    assert recognizer.received == [SPEECH * 4 + SILENCE * 2]
    assert synth.spoken == ["погода ясная"]
    assert len(sink.played) == 1
    assert sink.played[0].pcm == "погода ясная".encode()
    assert ("transcript", "какая погода") in events
    assert ("say", "погода ясная") in events
    assert wake.resets == 1


@pytest.mark.asyncio
async def test_empty_transcript_skips_reply() -> None:
    frames = [WAKE, SPEECH, SPEECH, SILENCE, SILENCE]
    called = False

    async def on_transcript(text: str) -> str:
        nonlocal called
        called = True
        return "не должно произнестись"

    sink = FakeSink()
    pipeline = VoicePipeline(
        source=FakeAudioSource(frames),
        wake=FakeWakeWord({WAKE}),
        recognizer=FakeRecognizer("   "),  # пусто после strip
        synthesizer=FakeSynthesizer(),
        sink=sink,
        settings=_settings(),
        on_transcript=on_transcript,
    )
    await pipeline.run()

    assert called is False
    assert sink.played == []


@pytest.mark.asyncio
async def test_two_interactions_in_one_stream() -> None:
    phrase = [WAKE, SPEECH, SPEECH, SILENCE, SILENCE]
    source = FakeAudioSource(phrase + phrase)
    wake = FakeWakeWord({WAKE})
    sink = FakeSink()
    count = 0

    async def on_transcript(text: str) -> str:
        nonlocal count
        count += 1
        return f"ответ {count}"

    pipeline = VoicePipeline(
        source=source,
        wake=wake,
        recognizer=FakeRecognizer("вопрос"),
        synthesizer=FakeSynthesizer(),
        sink=sink,
        settings=_settings(),
        on_transcript=on_transcript,
    )
    await pipeline.run()

    assert count == 2
    assert [c.pcm for c in sink.played] == ["ответ 1".encode(), "ответ 2".encode()]
    assert wake.resets == 2


def test_factory_builds_fakes() -> None:
    settings = _settings()
    assert isinstance(build_source(settings), FakeAudioSource)
    assert isinstance(build_sink(settings), FakeSink)
    assert isinstance(build_wake(settings), FakeWakeWord)
    assert isinstance(build_recognizer(settings), FakeRecognizer)
