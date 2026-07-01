"""Core-сервис (Phase 1).

Крутит мозг (Claude tool-use) поверх шины: слушает запросы пользователя (user/request),
прогоняет их через Brain + ToolRouter (который шлёт команды агентам и ждёт ответы),
возвращает ответ. Плюс реестр устройств, аудит и периодический ping.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from anthropic import AsyncAnthropic

from christopher.core.audit import AuditLog
from christopher.core.brain import Brain
from christopher.core.registry import DeviceRegistry
from christopher.core.router import ToolRouter
from christopher.core.scheduler import ActionScheduler, parse_when
from christopher.shared.bus import Bus
from christopher.shared.config import BusSettings
from christopher.shared.logging import setup_logging
from christopher.shared.protocol import (
    AssistantReply,
    Capability,
    CapabilityManifest,
    Command,
    ConfirmDecision,
    PendingAction,
    Response,
    RiskLevel,
    UserMessage,
)
from christopher.shared.topics import (
    PREFIX,
    REGISTRY_WILDCARD,
    RESP_WILDCARD,
    USER_CONFIRM,
    USER_REQUEST,
    cmd_topic,
    user_reply_topic,
)

log = logging.getLogger("christopher.core")

CORE_ID = "christopher-core"


class Core:
    def __init__(self, settings: BusSettings) -> None:
        self.settings = settings
        self.registry = DeviceRegistry()
        self.audit = AuditLog(settings.audit_db)
        self._bus: Bus | None = None
        self._pending: dict[str, asyncio.Future[Response]] = {}
        # risky-действия, ждущие подтверждения: reply_id (=id UserMessage) → список действий
        self._pending_confirm: dict[str, list[PendingAction]] = {}
        self._scheduler: ActionScheduler | None = None
        self._tasks: set[asyncio.Task[None]] = set()

        self.router = ToolRouter(self.registry, self._call_device, self.audit)
        self.brain: Brain | None = None
        if os.getenv("ANTHROPIC_API_KEY"):
            self.brain = Brain(
                AsyncAnthropic(),
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                max_iterations=settings.llm_max_iterations,
            )
        else:
            log.warning("ANTHROPIC_API_KEY не задан — мозг отключён (только реестр/ping)")

    @property
    def _bus_or_raise(self) -> Bus:
        if self._bus is None:
            raise RuntimeError("Core: шина не подключена")
        return self._bus

    # --- вызов устройства с ожиданием ответа (для ToolRouter) ---
    async def _call_device(
        self, device_id: str, action: str, params: dict[str, object], requires_confirm: bool
    ) -> Response:
        cmd = Command(
            source=CORE_ID,
            target=device_id,
            action=action,
            params=dict(params),
            requires_confirm=requires_confirm,
        )
        future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
        self._pending[cmd.id] = future
        await self._bus_or_raise.publish_model(cmd_topic(device_id), cmd)
        try:
            return await asyncio.wait_for(future, timeout=self.settings.command_timeout)
        finally:
            self._pending.pop(cmd.id, None)

    # --- обработка запроса пользователя (в отдельной задаче) ---
    def _spawn_user_request(self, payload: bytes) -> None:
        msg = UserMessage.model_validate_json(payload)
        task = asyncio.create_task(self._process_user_request(msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _process_user_request(self, msg: UserMessage) -> None:
        log.info("← запрос пользователя (id=%s): %s", msg.id[:8], msg.text)
        pending: list[PendingAction] = []
        if self.brain is None:
            text = "Мозг недоступен: не задан ANTHROPIC_API_KEY."
        else:
            try:
                result = await self.brain.handle(msg.text, self.router)
                text, pending = result.text, result.pending
            except Exception as exc:  # noqa: BLE001 — не роняем Core на ошибке запроса
                log.exception("ошибка обработки запроса")
                text = f"Ошибка обработки запроса: {exc}"
        if pending:
            self._pending_confirm[msg.id] = pending
        reply = AssistantReply(correlation_id=msg.id, text=text, pending=pending)
        await self._bus_or_raise.publish_model(user_reply_topic(msg.id), reply)
        log.info("→ ответ (id=%s): %s", msg.id[:8], text)

    # --- обработка подтверждения risky-действий ---
    def _spawn_confirm(self, payload: bytes) -> None:
        decision = ConfirmDecision.model_validate_json(payload)
        task = asyncio.create_task(self._process_confirm(decision))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _process_confirm(self, decision: ConfirmDecision) -> None:
        pending = self._pending_confirm.pop(decision.reply_id, None)
        if not pending:
            text = "Нет действий, ожидающих подтверждения (возможно, устарело)."
        elif not decision.approved:
            text = "Отменено, ничего не выполнено."
        else:
            text = await self._run_confirmed(pending)
        reply = AssistantReply(correlation_id=decision.reply_id, text=text)
        await self._bus_or_raise.publish_model(user_reply_topic(decision.reply_id), reply)
        log.info("→ ответ на подтверждение (id=%s): %s", decision.reply_id[:8], text)

    async def _run_confirmed(self, pending: list[PendingAction]) -> str:
        lines: list[str] = []
        for pa in pending:
            out = await self.router.execute_confirmed(pa)
            if out.get("ok"):
                lines.append(f"✓ {pa.summary}")
            else:
                lines.append(f"✗ {pa.summary}: {out.get('error')}")
        return "\n".join(lines)

    # --- входящие сообщения ---
    def _handle_manifest(self, payload: bytes) -> None:
        manifest = CapabilityManifest.model_validate_json(payload)
        self.registry.update(manifest)
        status = "online" if manifest.online else "offline"
        caps = ", ".join(c.name for c in manifest.capabilities) or "—"
        log.info(
            "registry: %s [%s] %s | возможности: %s",
            manifest.device_id,
            manifest.platform,
            status,
            caps,
        )

    def _handle_response(self, payload: bytes) -> None:
        resp = Response.model_validate_json(payload)
        future = self._pending.get(resp.correlation_id)
        if future is not None and not future.done():
            future.set_result(resp)
            return
        # не наш pending (напр. ответ на ping) — просто логируем
        if resp.ok:
            log.info("← resp от %s (cmd=%s): %s", resp.source, resp.correlation_id[:8], resp.result)
        else:
            log.warning(
                "← resp от %s (cmd=%s) ОШИБКА: %s", resp.source, resp.correlation_id[:8], resp.error
            )

    # --- планировщик (Scheduler): отложенные/повторяющиеся действия ---
    async def _fire_scheduled(self, target: str, action: str, params: dict[str, object]) -> None:
        """Срабатывание задачи: публикуем команду устройству (пользователь авторизовал при
        планировании → requires_confirm=True). Fire-and-forget, ответа не ждём."""
        cmd = Command(
            source=CORE_ID, target=target, action=action, params=dict(params), requires_confirm=True
        )
        await self._bus_or_raise.publish_model(cmd_topic(target), cmd)

    async def _tool_schedule_action(self, params: dict[str, object]) -> dict[str, object]:
        target = str(params.get("target", "")).strip()
        action = str(params.get("action", "")).strip()
        if not target or not action:
            raise ValueError("нужны параметры target и action")
        payload = params.get("params") or {}
        if not isinstance(payload, dict):
            raise ValueError("params должен быть объектом")
        delay_raw = params.get("delay_seconds")
        at_raw = params.get("at")
        delay = int(delay_raw) if isinstance(delay_raw, (int, str)) else None
        at = str(at_raw) if at_raw is not None else None
        run_at = parse_when(delay, at)
        assert self._scheduler is not None
        job_id = self._scheduler.schedule_once(target, action, dict(payload), run_at)
        return {"id": job_id, "next_run": run_at.isoformat()}

    async def _tool_schedule_cron(self, params: dict[str, object]) -> dict[str, object]:
        target = str(params.get("target", "")).strip()
        action = str(params.get("action", "")).strip()
        cron = str(params.get("cron", "")).strip()
        if not target or not action or not cron:
            raise ValueError("нужны параметры target, action и cron")
        payload = params.get("params") or {}
        if not isinstance(payload, dict):
            raise ValueError("params должен быть объектом")
        assert self._scheduler is not None
        job_id = self._scheduler.schedule_cron(target, action, dict(payload), cron)
        return {"id": job_id, "cron": cron}

    async def _tool_cancel_action(self, params: dict[str, object]) -> dict[str, object]:
        job_id = str(params.get("id", "")).strip()
        if not job_id:
            raise ValueError("нужен параметр id")
        assert self._scheduler is not None
        return {"cancelled": self._scheduler.cancel(job_id)}

    async def _tool_list_actions(self, params: dict[str, object]) -> dict[str, object]:
        assert self._scheduler is not None
        return {"jobs": self._scheduler.list_jobs()}

    def _setup_scheduler(self) -> None:
        self._scheduler = ActionScheduler(self.settings.scheduler_db, self._fire_scheduled)
        self._scheduler.start()
        obj_schema = {"type": "object"}
        self.router.register_local(
            Capability(
                name="schedule_action",
                description=(
                    "Отложенное действие: выполнить action на устройстве target через "
                    "delay_seconds секунд ИЛИ в момент at (ISO-время). params — аргументы действия"
                ),
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "action": {"type": "string"},
                        "params": obj_schema,
                        "delay_seconds": {"type": "integer"},
                        "at": {"type": "string"},
                    },
                    "required": ["target", "action"],
                },
            ),
            self._tool_schedule_action,
        )
        self.router.register_local(
            Capability(
                name="schedule_cron",
                description=(
                    "Повторяющееся действие по cron-выражению (5 полей). "
                    "Выполняет action на target с params по расписанию cron"
                ),
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "action": {"type": "string"},
                        "params": obj_schema,
                        "cron": {"type": "string"},
                    },
                    "required": ["target", "action", "cron"],
                },
            ),
            self._tool_schedule_cron,
        )
        self.router.register_local(
            Capability(
                name="cancel_action",
                description="Отменить запланированное действие по id (из list_actions)",
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            ),
            self._tool_cancel_action,
        )
        self.router.register_local(
            Capability(
                name="list_actions",
                description="Список запланированных действий (id, target, action, время)",
                risk=RiskLevel.safe,
            ),
            self._tool_list_actions,
        )

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.ping_interval)
            for device_id in self.registry.online_devices():
                cmd = Command(source=CORE_ID, target=device_id, action="ping")
                await self._bus_or_raise.publish_model(cmd_topic(device_id), cmd)

    async def run(self) -> None:
        log.info(
            "Core стартует, брокер %s:%s (tls=%s, модель=%s)",
            self.settings.broker_host,
            self.settings.broker_port,
            self.settings.tls,
            self.settings.llm_model,
        )
        async with Bus(self.settings, client_id=CORE_ID) as bus:
            self._bus = bus
            self._setup_scheduler()
            await bus.subscribe(REGISTRY_WILDCARD)
            await bus.subscribe(RESP_WILDCARD)
            await bus.subscribe(USER_REQUEST)
            await bus.subscribe(USER_CONFIRM)
            log.info("Core подключён, слушаю registry + responses + запросы + подтверждения")

            pinger = asyncio.create_task(self._ping_loop())
            try:
                async for message in bus.messages:
                    payload = message.payload
                    if not isinstance(payload, (bytes, bytearray)):
                        continue
                    data = bytes(payload)
                    topic = str(message.topic)
                    if topic == USER_REQUEST:
                        self._spawn_user_request(data)
                    elif topic == USER_CONFIRM:
                        self._spawn_confirm(data)
                    elif topic.startswith(f"{PREFIX}/registry/"):
                        self._handle_manifest(data)
                    elif topic.startswith(f"{PREFIX}/resp/"):
                        self._handle_response(data)
            finally:
                pinger.cancel()
                if self._scheduler is not None:
                    self._scheduler.shutdown()
                self.audit.close()


async def run() -> None:
    setup_logging()
    await Core(BusSettings()).run()


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
