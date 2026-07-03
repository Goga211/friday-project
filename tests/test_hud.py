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


# --- REST API (iPhone Shortcuts) ---

_TOKEN = "test-token-123"


class _EchoBus:
    """Фейковая шина: на запрос тут же «отвечает» через _on_bus_message (как Core)."""

    def __init__(self, hud: HudApp) -> None:
        self._hud = hud

    async def publish_model(
        self, topic: str, model: BaseModel, qos: int = 1, retain: bool = False
    ) -> None:
        if topic == USER_REQUEST:
            reply = AssistantReply(
                correlation_id=model.id,
                text=f"эхо: {getattr(model, 'text', '')}",
            )
        elif topic == USER_CONFIRM:
            reply = AssistantReply(
                correlation_id=model.reply_id,
                text="✓ выполнено" if model.approved else "отменено",
            )
        else:
            return
        await self._hud._on_bus_message(
            f"friday/user/reply/{reply.correlation_id}", reply.model_dump_json().encode()
        )


@pytest.fixture()
def api_client() -> Any:
    app = create_app(BusSettings(hud_token=_TOKEN), start_bus=False)
    with TestClient(app) as test_client:
        hud: HudApp = test_client.app.state.hud  # type: ignore[union-attr]
        hud._bus = _EchoBus(hud)  # type: ignore[assignment]
        yield test_client


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


def test_api_message_roundtrip(api_client: TestClient) -> None:
    resp = api_client.post("/api/message", json={"text": "привет"}, headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "эхо: привет"
    assert body["pending"] == []
    assert body["reply_id"]


def test_api_confirm_roundtrip(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/confirm", json={"reply_id": "r-9", "approved": True}, headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "✓ выполнено"


def test_api_rejects_wrong_token(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/message", json={"text": "x"}, headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 401


def test_api_rejects_missing_token(api_client: TestClient) -> None:
    assert api_client.post("/api/message", json={"text": "x"}).status_code == 401


def test_api_disabled_without_configured_token() -> None:
    # FRIDAY_HUD_TOKEN не задан → API отключён (503), даже с каким-то токеном в запросе
    app = create_app(BusSettings(hud_token=None), start_bus=False)
    with TestClient(app) as no_token_client:
        resp = no_token_client.post("/api/message", json={"text": "x"}, headers=_auth())
    assert resp.status_code == 503


def test_api_empty_text_rejected(api_client: TestClient) -> None:
    resp = api_client.post("/api/message", json={"text": "  "}, headers=_auth())
    assert resp.status_code == 422


def test_api_message_503_without_bus(api_client: TestClient) -> None:
    hud: HudApp = api_client.app.state.hud  # type: ignore[union-attr]
    hud._bus = None
    resp = api_client.post("/api/message", json={"text": "x"}, headers=_auth())
    assert resp.status_code == 503
