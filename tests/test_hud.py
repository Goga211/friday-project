"""Тесты веб-чата (HUD): страница, WebSocket-мост, подтверждения — без брокера."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from friday.hud.app import HudApp, create_app, reply_payload
from friday.shared.config import BusSettings
from friday.shared.protocol import AssistantReply, PendingAction, RiskLevel
from friday.shared.topics import USER_CONFIRM, USER_REQUEST


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, BaseModel]] = []

    async def publish_model(
        self, topic: str, model: BaseModel, qos: int = 1, retain: bool = False
    ) -> None:
        self.published.append((topic, model))


@pytest.fixture()
def client() -> Any:
    app = create_app(BusSettings(), start_bus=False)
    with TestClient(app) as test_client:  # контекст нужен для client.portal (async-вызовы)
        yield test_client


def _hud(client: TestClient) -> HudApp:
    hud: HudApp = client.app.state.hud  # type: ignore[union-attr]
    return hud


def test_index_serves_chat_page(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Пятница" in resp.text


def test_user_message_published_and_broadcast(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        fake = _FakeBus()
        _hud(client)._bus = fake  # type: ignore[assignment]

        ws.send_text(json.dumps({"type": "user", "text": "привет"}))
        echoed = json.loads(ws.receive_text())

        assert echoed == {"type": "user", "text": "привет"}
        assert len(fake.published) == 1
        topic, model = fake.published[0]
        assert topic == USER_REQUEST
        assert getattr(model, "text", None) == "привет"


def test_confirm_decision_published(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        fake = _FakeBus()
        _hud(client)._bus = fake  # type: ignore[assignment]

        ws.send_text(json.dumps({"type": "confirm", "reply_id": "r-1", "approved": True}))
        ws.send_text(json.dumps({"type": "user", "text": "ping"}))  # маркер обработки очереди
        ws.receive_text()

        topic, model = fake.published[0]
        assert topic == USER_CONFIRM
        assert getattr(model, "reply_id", None) == "r-1"
        assert getattr(model, "approved", None) is True


def test_reply_from_bus_broadcast_with_pending(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        hud = _hud(client)
        reply = AssistantReply(
            correlation_id="r-2",
            text="Нужно подтверждение",
            pending=[
                PendingAction(
                    device_id="desktop-x",
                    action="launch_app",
                    params={"name": "firefox"},
                    risk=RiskLevel.confirm,
                    summary="launch_app(name='firefox')",
                )
            ],
        )
        payload = reply.model_dump_json().encode()
        client.portal.call(hud._on_bus_message, "friday/user/reply/r-2", payload)

        data = json.loads(ws.receive_text())
        assert data["type"] == "assistant"
        assert data["reply_id"] == "r-2"
        assert data["pending"] == ["[confirm] launch_app(name='firefox')"]


def test_reply_payload_shape() -> None:
    reply = AssistantReply(correlation_id="abc", text="готово")
    assert reply_payload(reply) == {
        "type": "assistant",
        "text": "готово",
        "reply_id": "abc",
        "pending": [],
    }


def test_broken_json_from_client_ignored(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        fake = _FakeBus()
        _hud(client)._bus = fake  # type: ignore[assignment]

        ws.send_text("не json {{{")
        ws.send_text(json.dumps({"type": "user", "text": "живой?"}))
        echoed = json.loads(ws.receive_text())

        assert echoed["text"] == "живой?"  # битое сообщение не уронило соединение
