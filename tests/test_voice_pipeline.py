"""Юнит-тесты голосового пайплайна на фейках (без микрофона и облака)."""

from __future__ import annotations

import array
import asyncio

import pytest

from christopher.agents.voice.config import VoiceSettings
from christopher.agents.voice.factory import build_recognizer, build_sink, build_source, build_wake
from christopher.agents.voice.interfaces import AudioClip
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


class _SlowSink:
    """Играет клип несколько тактов цикла событий, чтобы barge-in успел его прервать."""

    def __init__(self, frames_to_play: int = 10) -> None:
        self._frames_to_play = frames_to_play
        self.played: list[AudioClip] = []
        self.stops = 0
        self.interrupted = 0

    async def play(self, clip: AudioClip) -> None:
        try:
            for _ in range(self._frames_to_play):
                await asyncio.sleep(0)
            self.played.append(clip)
        except asyncio.CancelledError:
            self.interrupted += 1
            raise

    async def stop(self) -> None:
        self.stops += 1


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
    assert wake.resets >= 1


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
    # пауза между ответом и новым обращением — ответ первого раунда успевает доиграть
    source = FakeAudioSource(phrase + [SILENCE, SILENCE] + phrase)
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


@pytest.mark.asyncio
async def test_barge_in_interrupts_playback() -> None:
    # Ответ длинный (много кадров), пользователь перебивает новым wake-word во время речи.
    phrase = [WAKE, SPEECH, SPEECH, SILENCE, SILENCE]
    interrupt = [WAKE, SPEECH, SPEECH, SILENCE, SILENCE]
    source = FakeAudioSource(phrase + interrupt)
    wake = FakeWakeWord({WAKE})
    sink = _SlowSink(frames_to_play=10)  # играет дольше, чем длится пауза до перебивания
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

    assert count == 2  # оба обращения обработаны
    assert sink.stops >= 1  # первое воспроизведение прервано barge-in
    assert sink.interrupted >= 1  # play первого ответа не доиграл до конца


@pytest.mark.asyncio
async def test_barge_in_disabled_ignores_wake_during_speech() -> None:
    phrase = [WAKE, SPEECH, SPEECH, SILENCE, SILENCE]
    source = FakeAudioSource(phrase + [WAKE, WAKE])
    wake = FakeWakeWord({WAKE})
    sink = _SlowSink(frames_to_play=10)

    async def on_transcript(text: str) -> str:
        return "ответ"

    pipeline = VoicePipeline(
        source=source,
        wake=wake,
        recognizer=FakeRecognizer("вопрос"),
        synthesizer=FakeSynthesizer(),
        sink=sink,
        settings=_settings(barge_in=False),
        on_transcript=on_transcript,
    )
    await pipeline.run()

    assert sink.stops == 0  # без barge-in воспроизведение не прерывается


def test_factory_builds_fakes() -> None:
    settings = _settings()
    assert isinstance(build_source(settings), FakeAudioSource)
    assert isinstance(build_sink(settings), FakeSink)
    assert isinstance(build_wake(settings), FakeWakeWord)
    assert isinstance(build_recognizer(settings), FakeRecognizer)


def test_pushtotalk_triggers_once_per_press() -> None:
    from christopher.agents.voice.providers.pushtotalk import PushToTalkWakeWord

    ptt = PushToTalkWakeWord()
    assert ptt.process(b"") == 0.0  # без нажатия — молчок
    ptt._pending.set()  # эмулируем нажатие Enter
    assert ptt.process(b"") == 1.0  # одно срабатывание
    assert ptt.process(b"") == 0.0  # одно нажатие = одна запись
