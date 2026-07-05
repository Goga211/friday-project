"""Веб-чат «Пятницы» — зачаток HUD (Phase 1) + REST API для iPhone (Phase 3).

Лёгкая страница поверх шины: шлёт запросы мозгу (user/request), показывает ответы
(user/reply/#), голосовые события (voice/transcript, voice/say) и кнопки подтверждения
risky-действий. Система однопользовательская: все открытые вкладки видят один диалог
(broadcast). Разрыв шины переживается через run_with_reconnect — вкладки не отваливаются.

REST API (/api/message, /api/confirm) — «пульт» для iPhone через Siri Shortcuts +
Tailscale: синхронный запрос-ответ поверх той же шины, защищён Bearer-токеном
(FRIDAY_HUD_TOKEN; не задан → API отключён, безопасный дефолт).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiomqtt
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from friday.shared import aio
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
        # REST API: correlation_id → future с ответом ассистента (для /api/*)
        self._api_pending: dict[str, asyncio.Future[AssistantReply]] = {}

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

    # --- REST API (iPhone Shortcuts): синхронный запрос-ответ поверх шины ---
    async def _publish_or_503(self, topic: str, model: BaseModel) -> None:
        if self._bus is None:
            raise HTTPException(status_code=503, detail="шина недоступна")
        try:
            await self._bus.publish_model(topic, model)
        except aiomqtt.MqttError as exc:
            raise HTTPException(status_code=503, detail="разрыв шины — попробуй ещё раз") from exc

    async def _request_reply(
        self, correlation_id: str, topic: str, model: BaseModel
    ) -> AssistantReply:
        """Опубликовать запрос и дождаться ответа ассистента по correlation_id.

        Ожидание регистрируется ДО публикации — иначе ответ, прилетевший раньше
        await'а, молча потерялся бы.
        """
        future: asyncio.Future[AssistantReply] = asyncio.get_running_loop().create_future()
        self._api_pending[correlation_id] = future
        try:
            await self._publish_or_503(topic, model)
            return await asyncio.wait_for(future, timeout=self._settings.hud_api_timeout)
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="мозг не ответил вовремя") from exc
        finally:
            self._api_pending.pop(correlation_id, None)

    async def api_message(self, text: str) -> AssistantReply:
        msg = UserMessage(text=text)
        # вкладки браузера видят разговор с телефона — единый однопользовательский диалог
        await self._broadcast({"type": "user", "text": text, "via": "api"})
        return await self._request_reply(msg.id, USER_REQUEST, msg)

    async def api_confirm(self, reply_id: str, approved: bool) -> AssistantReply:
        decision = ConfirmDecision(reply_id=reply_id, approved=approved)
        return await self._request_reply(reply_id, USER_CONFIRM, decision)

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
            future = self._api_pending.get(reply.correlation_id)
            if future is not None and not future.done():
                future.set_result(reply)
            await self._broadcast(reply_payload(reply))
        elif topic == VOICE_TRANSCRIPT:
            heard = VoiceTranscript.model_validate_json(data)
            await self._broadcast({"type": "user", "text": heard.text, "via": "voice"})
        elif topic == VOICE_SAY:
            said = VoiceSay.model_validate_json(data)
            await self._broadcast({"type": "voice_say", "text": said.text})


class ApiMessageRequest(BaseModel):
    text: str


class ApiConfirmRequest(BaseModel):
    reply_id: str
    approved: bool


class ApiReply(BaseModel):
    text: str
    reply_id: str
    pending: list[str]


def _api_reply(reply: AssistantReply) -> ApiReply:
    return ApiReply(
        text=reply.text,
        reply_id=reply.correlation_id,
        pending=[f"[{pa.risk.value}] {pa.summary}" for pa in reply.pending],
    )


def create_app(settings: BusSettings | None = None, *, start_bus: bool = True) -> FastAPI:
    """Собрать FastAPI-приложение HUD. start_bus=False — для тестов без брокера."""
    bus_settings = settings or BusSettings()
    hud = HudApp(bus_settings)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        """Bearer-охрана /api/*: без настроенного токена API выключен (503), не дыра."""
        if not bus_settings.hud_token:
            raise HTTPException(
                status_code=503, detail="REST API отключён — задай FRIDAY_HUD_TOKEN"
            )
        provided = ""
        if authorization and authorization.startswith("Bearer "):
            provided = authorization.removeprefix("Bearer ").strip()
        if not secrets.compare_digest(provided, bus_settings.hud_token):
            raise HTTPException(status_code=401, detail="неверный токен")

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

    @app.post("/api/message", response_model=ApiReply, dependencies=[Depends(require_token)])
    async def api_message(req: ApiMessageRequest) -> ApiReply:
        text = req.text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="text пуст")
        return _api_reply(await hud.api_message(text))

    @app.post("/api/confirm", response_model=ApiReply, dependencies=[Depends(require_token)])
    async def api_confirm(req: ApiConfirmRequest) -> ApiReply:
        if not req.reply_id.strip():
            raise HTTPException(status_code=422, detail="reply_id пуст")
        return _api_reply(await hud.api_confirm(req.reply_id.strip(), req.approved))

    return app


def main() -> None:
    load_env()
    setup_logging()
    settings = BusSettings()
    log.info("HUD стартует: http://%s:%s", settings.hud_host, settings.hud_port)
    config = uvicorn.Config(
        create_app(settings),
        host=settings.hud_host,
        port=settings.hud_port,
        log_level="warning",  # свой logging уже настроен, uvicorn не дублирует
    )
    # не uvicorn.run(): он на Windows принудительно берёт Proactor-луп, на котором
    # aiomqtt падает — серверим в своём селекторном лупе (см. shared/aio.py)
    aio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
