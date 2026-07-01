"""Тест устного подтверждения risky-действий в голосовом агенте (без шины и микрофона)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from christopher.agents.voice.app import VoiceApp, _is_affirmative
from christopher.agents.voice.config import VoiceSettings
from christopher.shared.config import BusSettings
from christopher.shared.protocol import (
    AssistantReply,
    ConfirmDecision,
    PendingAction,
    RiskLevel,
    UserMessage,
)
from christopher.shared.topics import USER_CONFIRM, USER_REQUEST


class _FakeBus:
    """Запоминает опубликованные модели по топикам (для проверки исходящих сообщений)."""

    def __init__(self) -> None:
        self.published: dict[str, list[BaseModel]] = {}

    async def publish_model(
        self, topic: str, model: BaseModel, qos: int = 1, retain: bool = False
    ) -> None:
        self.published.setdefault(topic, []).append(model)


def _app_with_bus() -> tuple[VoiceApp, _FakeBus]:
    app = VoiceApp(BusSettings(_env_file=None), VoiceSettings(_env_file=None))  # type: ignore[call-arg]
    bus = _FakeBus()
    app._bus = bus  # type: ignore[assignment]
    return app, bus


def _pending_action() -> PendingAction:
    return PendingAction(
        device_id="desktop-x",
        action="run_command",
        params={"command": "shutdown"},
        risk=RiskLevel.dangerous,
        summary="run_command(command='shutdown')",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("да", True),
        ("Да, конечно", True),
        ("подтверждаю", True),
        ("давай выключай", True),
        ("нет", False),
        ("не надо", False),
        ("отмена", False),
        ("", False),
    ],
)
def test_is_affirmative(text: str, expected: bool) -> None:
    assert _is_affirmative(text) is expected


@pytest.mark.asyncio
async def test_voice_confirm_approved_flow() -> None:
    app, bus = _app_with_bus()

    # 1) фраза с risky-действием: мозг отвечает pending → ждём устного подтверждения
    task = asyncio.create_task(app._on_transcript("выключи компьютер"))
    await asyncio.sleep(0)
    msg = bus.published[USER_REQUEST][-1]
    assert isinstance(msg, UserMessage)
    reply = AssistantReply(
        correlation_id=msg.id, text="Нужно подтвердить выключение", pending=[_pending_action()]
    )
    app._handle_reply(reply.model_dump_json().encode())
    said = await task
    assert said == "Нужно подтвердить выключение"
    assert app._awaiting_confirm == msg.id

    # 2) следующая фраза «да» → публикуется ConfirmDecision(approved=True), ждём результат
    task2 = asyncio.create_task(app._on_transcript("да, выключай"))
    await asyncio.sleep(0)
    decision = bus.published[USER_CONFIRM][-1]
    assert isinstance(decision, ConfirmDecision)
    assert decision.approved is True
    assert decision.reply_id == msg.id
    result = AssistantReply(correlation_id=msg.id, text="✓ выключаю")
    app._handle_reply(result.model_dump_json().encode())
    said2 = await task2
    assert said2 == "✓ выключаю"
    assert app._awaiting_confirm is None  # состояние сброшено


@pytest.mark.asyncio
async def test_voice_confirm_declined_flow() -> None:
    app, bus = _app_with_bus()

    task = asyncio.create_task(app._on_transcript("удали файлы"))
    await asyncio.sleep(0)
    msg = bus.published[USER_REQUEST][-1]
    app._handle_reply(
        AssistantReply(
            correlation_id=msg.id, text="Подтверди удаление", pending=[_pending_action()]
        ).model_dump_json().encode()
    )
    await task

    task2 = asyncio.create_task(app._on_transcript("нет, не надо"))
    await asyncio.sleep(0)
    decision = bus.published[USER_CONFIRM][-1]
    assert isinstance(decision, ConfirmDecision)
    assert decision.approved is False
    app._handle_reply(
        AssistantReply(
            correlation_id=decision.reply_id, text="Отменено, ничего не выполнено."
        ).model_dump_json().encode()
    )
    assert await task2 == "Отменено, ничего не выполнено."


@pytest.mark.asyncio
async def test_plain_request_does_not_await_confirm() -> None:
    app, bus = _app_with_bus()

    task = asyncio.create_task(app._on_transcript("который час"))
    await asyncio.sleep(0)
    msg = bus.published[USER_REQUEST][-1]
    app._handle_reply(
        AssistantReply(correlation_id=msg.id, text="Полдень").model_dump_json().encode()
    )
    said = await task
    assert said == "Полдень"
    assert app._awaiting_confirm is None
    assert USER_CONFIRM not in bus.published
