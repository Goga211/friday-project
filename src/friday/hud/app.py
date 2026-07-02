"""Веб-чат «Пятницы» — зачаток HUD (Phase 1).

Лёгкая страница поверх шины: шлёт запросы мозгу (user/request), показывает ответы
(user/reply/#), голосовые события (voice/transcript, voice/say) и кнопки подтверждения
risky-действий. Система однопользовательская: все открытые вкладки видят один диалог
(broadcast). Разрыв шины переживается через run_with_reconnect — вкладки не отваливаются.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiomqtt
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from friday.shared.bus import Bus, run_with_reconnect
from friday.shared.config import BusSettings
from friday.shared.env import load_env
from friday.shared.logging import setup_logging
from friday.shared.protocol import (
    AssistantReply,
    ConfirmDecision,
    UserMessage,
    VoiceSay,
    VoiceTranscript,
)
from friday.shared.topics import (
    PREFIX,
    USER_CONFIRM,
    USER_REPLY_WILDCARD,
    USER_REQUEST,
    VOICE_SAY,
    VOICE_TRANSCRIPT,
)

log = logging.getLogger("friday.hud")

HUD_ID = "friday-hud"
_STATIC = Path(__file__).parent / "static"


def reply_payload(reply: AssistantReply) -> dict[str, Any]:
    """AssistantReply → JSON для браузера (pending — человекочитаемые сводки действий)."""
    return {
        "type": "assistant",
        "text": reply.text,
        "reply_id": reply.correlation_id,
        "pending": [f"[{pa.risk.value}] {pa.summary}" for pa in reply.pending],
    }


class HudApp:
    """Мост шина ↔ WebSocket-клиенты (вкладки браузера)."""

    def __init__(self, settings: BusSettings) -> None:
        self._settings = settings
        self._bus: Bus | None = None
        self._clients: set[WebSocket] = set()

    # --- рассылка в браузеры ---
    async def _broadcast(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        for ws in list(self._clients):
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001 — отвалившаяся вкладка не мешает остальным
                self._clients.discard(ws)

    # --- сообщения из браузера ---
    async def handle_ws(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        try:
            while True:
                raw = await ws.receive_text()
                await self._on_client_message(raw)
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.discard(ws)

    async def _on_client_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("HUD: битый JSON от клиента — игнорирую")
            return
        kind = data.get("type")
        if kind == "user":
            text = str(data.get("text", "")).strip()
            if not text:
                return
            await self._broadcast({"type": "user", "text": text})
            await self._publish(USER_REQUEST, UserMessage(text=text))
        elif kind == "confirm":
            reply_id = str(data.get("reply_id", "")).strip()
            approved = bool(data.get("approved"))
            if reply_id:
                await self._publish(
                    USER_CONFIRM, ConfirmDecision(reply_id=reply_id, approved=approved)
                )
        else:
            log.warning("HUD: неизвестный тип сообщения от клиента: %r", kind)

    async def _publish(self, topic: str, model: BaseModel) -> None:
        if self._bus is None:
            await self._broadcast({"type": "status", "text": "Шина недоступна — запрос потерян"})
            return
        try:
            await self._bus.publish_model(topic, model)
        except aiomqtt.MqttError:
            await self._broadcast(
                {"type": "status", "text": "Разрыв шины — запрос не доставлен, переподключаюсь"}
            )

    # --- сессия на шине (крутится под run_with_reconnect) ---
    async def bus_session(self) -> None:
        async with Bus(self._settings, client_id=HUD_ID) as bus:
            self._bus = bus
            await bus.subscribe(USER_REPLY_WILDCARD)
            await bus.subscribe(VOICE_TRANSCRIPT)
            await bus.subscribe(VOICE_SAY)
            log.info("HUD подключён к шине (reply + голосовые события)")
            await self._broadcast({"type": "status", "text": "Подключено к шине"})
            async for message in bus.messages:
                payload = message.payload
                if not isinstance(payload, (bytes, bytearray)):
                    continue
                await self._on_bus_message(str(message.topic), bytes(payload))

    async def _on_bus_message(self, topic: str, data: bytes) -> None:
        if topic.startswith(f"{PREFIX}/user/reply/"):
            reply = AssistantReply.model_validate_json(data)
            await self._broadcast(reply_payload(reply))
        elif topic == VOICE_TRANSCRIPT:
            heard = VoiceTranscript.model_validate_json(data)
            await self._broadcast({"type": "user", "text": heard.text, "via": "voice"})
        elif topic == VOICE_SAY:
            said = VoiceSay.model_validate_json(data)
            await self._broadcast({"type": "voice_say", "text": said.text})


def create_app(settings: BusSettings | None = None, *, start_bus: bool = True) -> FastAPI:
    """Собрать FastAPI-приложение HUD. start_bus=False — для тестов без брокера."""
    bus_settings = settings or BusSettings()
    hud = HudApp(bus_settings)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if start_bus:
            task = asyncio.create_task(
                run_with_reconnect(
                    hud.bus_session,
                    initial_delay=bus_settings.reconnect_initial_delay,
                    max_delay=bus_settings.reconnect_max_delay,
                )
            )
        yield
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Пятница — HUD", lifespan=lifespan)
    app.state.hud = hud

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await hud.handle_ws(ws)

    return app


def main() -> None:
    load_env()
    setup_logging()
    settings = BusSettings()
    log.info("HUD стартует: http://%s:%s", settings.hud_host, settings.hud_port)
    uvicorn.run(
        create_app(settings),
        host=settings.hud_host,
        port=settings.hud_port,
        log_level="warning",  # свой logging уже настроен, uvicorn не дублирует
    )


if __name__ == "__main__":
    main()
