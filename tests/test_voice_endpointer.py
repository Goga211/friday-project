"""Юнит-тесты эндпоинтера конца фразы (VAD по энергии)."""

from __future__ import annotations

import array

from friday.agents.voice.vad import Endpointer, frame_rms

FRAME_SAMPLES = 1280


def _frame(value: int, n: int = FRAME_SAMPLES) -> bytes:
    return array.array("h", [value] * n).tobytes()


SILENCE = _frame(0)
SPEECH = _frame(5000)


def _endpointer(**overrides: float) -> Endpointer:
    params: dict[str, float] = {
        "sample_rate": 16000,
        "frame_samples": FRAME_SAMPLES,
        "energy_threshold": 400.0,
        "silence_seconds": 0.16,
        "min_phrase_seconds": 0.16,
        "max_phrase_seconds": 2.0,
    }
    params.update(overrides)
    return Endpointer(**params)  # type: ignore[arg-type]


def test_frame_rms_silence_is_zero() -> None:
    assert frame_rms(SILENCE) == 0.0


def test_frame_rms_loud_is_high() -> None:
    assert frame_rms(SPEECH) > 400.0


def test_finishes_after_trailing_silence() -> None:
    ep = _endpointer()
    assert ep.feed(SPEECH) is False  # 0.08 с
    assert ep.feed(SPEECH) is False  # 0.16 с, тишины нет
    assert ep.feed(SILENCE) is False  # тишина 0.08 < 0.16
    assert ep.feed(SILENCE) is True  # тишина 0.16 → конец


def test_ignores_silence_before_speech() -> None:
    ep = _endpointer(silence_seconds=0.08)
    assert ep.feed(SILENCE) is False
    assert ep.feed(SILENCE) is False  # речи ещё не было — не финишируем
    assert ep.feed(SPEECH) is False
    assert ep.feed(SILENCE) is True


def test_max_duration_caps_recording() -> None:
    ep = _endpointer(max_phrase_seconds=0.24, silence_seconds=99.0)
    assert ep.feed(SPEECH) is False  # 0.08
    assert ep.feed(SPEECH) is False  # 0.16
    assert ep.feed(SPEECH) is True  # 0.24 = потолок


def test_reset_clears_state() -> None:
    ep = _endpointer(max_phrase_seconds=0.24, silence_seconds=99.0)
    ep.feed(SPEECH)
    ep.feed(SPEECH)
    ep.reset()
    assert ep.feed(SPEECH) is False  # после сброса счётчик начался заново
