"""Тесты run_with_reconnect: backoff при MqttError, чужие ошибки — наружу.

Плюс регрессия Core: разрыв шины в момент отправки ответа пользователю не должен
ронять задачу-обработчик (ответ теряется с warning-ом, но без «exception never
retrieved»).
"""

from __future__ import annotations

from pathlib import Path

import aiomqtt
import pytest

from friday.core.app import Core
from friday.shared.bus import run_with_reconnect
from friday.shared.config import BusSettings
from friday.shared.protocol import AssistantReply


class _SleepRecorder:
    """Фейковый sleep: не ждёт, только записывает задержки."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def test_returns_after_normal_completion() -> None:
    calls = 0

    async def session() -> None:
        nonlocal calls
        calls += 1

    sleeper = _SleepRecorder()
    await run_with_reconnect(session, initial_delay=1.0, max_delay=60.0, sleep=sleeper)

    assert calls == 1
    assert sleeper.delays == []


async def test_reconnects_with_exponential_backoff() -> None:
    calls = 0

    async def session() -> None:
        nonlocal calls
        calls += 1
        if calls < 4:
            raise aiomqtt.MqttError("Disconnected during message iteration")

    sleeper = _SleepRecorder()
    await run_with_reconnect(session, initial_delay=1.0, max_delay=60.0, sleep=sleeper)

    assert calls == 4
    assert sleeper.delays == [1.0, 2.0, 4.0]


async def test_backoff_caps_at_max_delay() -> None:
    calls = 0

    async def session() -> None:
        nonlocal calls
        calls += 1
        if calls < 6:
            raise aiomqtt.MqttError("broker down")

    sleeper = _SleepRecorder()
    await run_with_reconnect(session, initial_delay=1.0, max_delay=4.0, sleep=sleeper)

    assert sleeper.delays == [1.0, 2.0, 4.0, 4.0, 4.0]


async def test_mqtt_error_inside_exception_group_reconnects() -> None:
    """Сессия на TaskGroup заворачивает MqttError в ExceptionGroup — тоже реконнект."""
    calls = 0

    async def session() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExceptionGroup("session failed", [aiomqtt.MqttError("disconnected")])

    sleeper = _SleepRecorder()
    await run_with_reconnect(session, initial_delay=1.0, max_delay=60.0, sleep=sleeper)

    assert calls == 2
    assert sleeper.delays == [1.0]


async def test_foreign_error_propagates() -> None:
    async def session() -> None:
        raise RuntimeError("это баг, а не сеть")

    sleeper = _SleepRecorder()
    with pytest.raises(RuntimeError):
        await run_with_reconnect(session, initial_delay=1.0, max_delay=60.0, sleep=sleeper)
    assert sleeper.delays == []


async def test_mixed_exception_group_propagates() -> None:
    """Если в группе не только MqttError — это не сетевой сбой, наружу целиком."""

    async def session() -> None:
        raise ExceptionGroup(
            "session failed",
            [aiomqtt.MqttError("disconnected"), RuntimeError("баг в пайплайне")],
        )

    sleeper = _SleepRecorder()
    with pytest.raises(ExceptionGroup):
        await run_with_reconnect(session, initial_delay=1.0, max_delay=60.0, sleep=sleeper)
    assert sleeper.delays == []


async def test_delay_resets_after_stable_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Долго прожившая сессия сбрасывает backoff к initial_delay."""
    now = 0.0
    monkeypatch.setattr("friday.shared.bus.time.monotonic", lambda: now)

    calls = 0

    async def session() -> None:
        nonlocal calls, now
        calls += 1
        if calls == 3:  # третья сессия «жила» дольше порога стабильности
            now += 120.0
        if calls < 4:
            raise aiomqtt.MqttError("flaky broker")

    sleeper = _SleepRecorder()
    await run_with_reconnect(session, initial_delay=1.0, max_delay=60.0, sleep=sleeper)

    # после двух быстрых падений backoff растёт (1, 2), после стабильной сессии — снова 1
    assert sleeper.delays == [1.0, 2.0, 1.0]


class _DeadBus:
    """Шина, у которой соединение уже умерло: любой publish — MqttError."""

    async def publish_model(self, *args: object, **kwargs: object) -> None:
        raise aiomqtt.MqttError("Disconnected")


async def test_core_reply_publish_survives_bus_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Разрыв шины при отправке ответа пользователю не роняет задачу-обработчик."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    core = Core(
        BusSettings(
            audit_db=str(tmp_path / "audit.db"),
            scheduler_db=str(tmp_path / "jobs.db"),
        )
    )
    core._bus = _DeadBus()  # type: ignore[assignment]
    try:
        # не должно бросить — потеря ответа логируется, но не валит задачу
        await core._publish_reply("reply-1", AssistantReply(correlation_id="reply-1", text="hi"))
    finally:
        core.audit.close()
