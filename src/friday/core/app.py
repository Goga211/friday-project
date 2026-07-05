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

import aiomqtt
from anthropic import AsyncAnthropic

from friday.core.audit import AuditLog
from friday.core.brain import Brain
from friday.core.memory import KINDS, Fact, MemoryStore, select_relevant
from friday.core.push import push_notify
from friday.core.registry import DeviceRegistry
from friday.core.router import ToolRouter
from friday.core.scheduler import ActionScheduler, parse_when
from friday.shared import aio
from friday.shared.bus import Bus, run_with_reconnect
from friday.shared.config import BusSettings
from friday.shared.env import load_env
from friday.shared.logging import setup_logging
from friday.shared.protocol import (
    AssistantReply,
    Capability,
    CapabilityManifest,
    Command,
    ConfirmDecision,
    Event,
    PendingAction,
    Response,
    RiskLevel,
    UserMessage,
)
from friday.shared.topics import (
    EVENT_WILDCARD,
    PREFIX,
    REGISTRY_WILDCARD,
    RESP_WILDCARD,
    USER_CONFIRM,
    USER_REQUEST,
    cmd_topic,
    user_reply_topic,
)
from friday.shared.wol import send_magic_packet

log = logging.getLogger("friday.core")

CORE_ID = "friday-core"

# Тип события от desktop-агента: итог фоновой задачи Claude Code (см. claude_code.py)
CLAUDE_TASK_DONE = "claude_task_done"


class Core:
    def __init__(self, settings: BusSettings) -> None:
        self.settings = settings
        # Реестр персистентный (та же SQLite, что и аудит): alias/MAC выключенных
        # устройств переживают рестарт Hub'а — их можно будить по WoL.
        self.registry = DeviceRegistry(settings.audit_db)
        self.audit = AuditLog(settings.audit_db)
        self._bus: Bus | None = None
        self._pending: dict[str, asyncio.Future[Response]] = {}
        # risky-действия, ждущие подтверждения: reply_id (=id UserMessage) → список действий
        self._pending_confirm: dict[str, list[PendingAction]] = {}
        self._scheduler: ActionScheduler | None = None
        self._tasks: set[asyncio.Task[None]] = set()

        self.router = ToolRouter(self.registry, self._call_device, self.audit)
        self._setup_device_tools()
        self.brain: Brain | None = None
        self.memory = MemoryStore(settings.audit_db)
        self._llm_client: AsyncAnthropic | None = None
        if os.getenv("ANTHROPIC_API_KEY"):
            self._llm_client = AsyncAnthropic()
            self.brain = Brain(
                self._llm_client,
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                max_iterations=settings.llm_max_iterations,
                history_max_messages=settings.llm_history_max_messages,
            )
            # Контекст диалога переживает рестарт: поднимаем последние реплики из SQLite.
            restored = self.audit.recent_dialog(settings.llm_history_max_messages)
            if restored:
                self.brain.preload_history(restored)
                log.info("восстановлен контекст диалога: %d реплик", len(restored))
            # Долгосрочная память: recall/forget зовут модель-селектор — только при ключе.
            self._setup_memory_tools()
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

    async def _publish_reply(self, reply_id: str, reply: AssistantReply) -> None:
        """Отправить ответ пользователю. Разрыв шины в этот момент не должен ронять
        задачу-обработчик «exception never retrieved»-ом: ответ уже не доставить
        (реконнект идёт в фоне), но потерю фиксируем в логе, а не молча."""
        try:
            await self._bus_or_raise.publish_model(user_reply_topic(reply_id), reply)
        except aiomqtt.MqttError:
            log.warning("разрыв шины: ответ пользователю (id=%s) не доставлен", reply_id[:8])

    # --- события агентов (фоновые задачи Claude Code и т.п.) ---
    def _spawn_event(self, payload: bytes) -> None:
        try:
            event = Event.model_validate_json(payload)
        except ValueError:
            log.warning("некорректное событие на шине, пропускаю")
            return
        if event.type != CLAUDE_TASK_DONE:
            log.debug("событие %s от %s — обработчика нет", event.type, event.source)
            return
        task = asyncio.create_task(self._announce_task_result(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _announce_task_result(self, event: Event) -> None:
        """Доставить пользователю итог фоновой задачи Claude Code: контекст + push + голос."""
        ok = bool(event.data.get("ok"))
        result = str(event.data.get("result") or "").strip()
        rec = self.registry.get(event.source)
        label = (rec.manifest.alias if rec is not None else None) or event.source
        status = "выполнена" if ok else "завершилась с ошибкой"
        text = f"Фоновая задача Claude Code на «{label}» {status}."
        if result:
            text += f" Итог: {result}"

        # 1) контекст мозга: следующий вопрос «ну что там?» должен видеть итог
        if self.brain is not None:
            self.brain.remember("[система] завершилась фоновая задача на ПК", text[:2000])
        # 2) push на телефон (если настроен)
        if self.settings.push_url:
            try:
                await push_notify(
                    self.settings.push_url, text[:1500], title="Пятница: задача на ПК"
                )
            except Exception as exc:  # noqa: BLE001 — недоставленный push не роняет Core
                log.warning("push об итоге задачи не доставлен: %s", exc)
        # 3) голосом (если голосовой агент онлайн; иначе execute вернёт ошибку — не страшно)
        say = await self.router.execute("say", {"text": text[:600]})
        if not say.get("ok"):
            log.info("итог задачи не озвучен: %s", say.get("error"))

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
            else:
                self.audit.record_dialog("user", msg.text)
                self.audit.record_dialog("assistant", text)
        if pending:
            self._pending_confirm[msg.id] = pending
        reply = AssistantReply(correlation_id=msg.id, text=text, pending=pending)
        await self._publish_reply(msg.id, reply)
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
        if pending is not None:
            # Итог подтверждения — тоже часть диалога: мозг должен знать, что действие
            # выполнено/отменено (иначе для него оно осталось «ждёт подтверждения»).
            user_word = "да" if decision.approved else "нет"
            if self.brain is not None:
                self.brain.remember(user_word, text)
            self.audit.record_dialog("user", user_word)
            self.audit.record_dialog("assistant", text)
        reply = AssistantReply(correlation_id=decision.reply_id, text=text)
        await self._publish_reply(decision.reply_id, reply)
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
        планировании → requires_confirm=True). Fire-and-forget, ответа не ждём.

        target из плана — лишь подсказка (мозг не всегда знает id устройств), поэтому
        резолвим реальное онлайн-устройство по возможности. Если его нет — НЕ теряем молча,
        а логируем предупреждение (задача сработала, но доставить некуда)."""
        device_id = self.router.resolve_target(target, action)
        if device_id is None:
            log.warning(
                "scheduler: '%s' сработало, но нет онлайн-устройства с возможностью "
                "(подсказка target=%s) — команда не отправлена",
                action,
                target,
            )
            return
        cmd = Command(
            source=CORE_ID,
            target=device_id,
            action=action,
            params=dict(params),
            requires_confirm=True,
        )
        try:
            await self._bus_or_raise.publish_model(cmd_topic(device_id), cmd)
        except aiomqtt.MqttError:
            log.warning(
                "scheduler: '%s' сработало в момент разрыва шины — команда до %s не дошла",
                action,
                device_id,
            )
            return
        log.info("scheduler: команда %s → %s отправлена", action, device_id)

    async def _tool_schedule_action(self, params: dict[str, object]) -> dict[str, object]:
        target = str(params.get("target", "")).strip()  # необязательная подсказка устройства
        action = str(params.get("action", "")).strip()
        if not action:
            raise ValueError("нужен параметр action (имя навыка)")
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
        target = str(params.get("target", "")).strip()  # необязательная подсказка устройства
        action = str(params.get("action", "")).strip()
        cron = str(params.get("cron", "")).strip()
        if not action or not cron:
            raise ValueError("нужны параметры action (имя навыка) и cron")
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

    # --- устройства: обзор и пробуждение (для мозга) ---
    async def _tool_wake_device(self, params: dict[str, object]) -> dict[str, object]:
        name = str(params.get("device", "")).strip()
        if not name:
            raise ValueError("нужен параметр device (алиас или id устройства)")
        record = self.registry.resolve(name)
        if record is None:
            raise ValueError(f"неизвестное устройство '{name}' (см. list_devices)")
        manifest = record.manifest
        label = manifest.alias or manifest.device_id
        if manifest.online:
            return {"already_online": True, "message": f"«{label}» уже онлайн"}
        if not manifest.mac:
            raise ValueError(f"у устройства «{label}» нет MAC в манифесте — WoL невозможен")
        send_magic_packet(manifest.mac, self.settings.wol_broadcast, self.settings.wol_port)
        return {
            "sent": True,
            "message": (
                f"магический пакет отправлен на «{label}» ({manifest.mac}) — "
                "устройство появится онлайн через ~1–2 минуты, если WoL включён в BIOS"
            ),
        }

    async def _tool_notify_phone(self, params: dict[str, object]) -> dict[str, object]:
        message = str(params.get("message", "")).strip()
        if not message:
            raise ValueError("нужен параметр message")
        raw_title = params.get("title")
        title = str(raw_title).strip() if raw_title else None
        assert self.settings.push_url is not None  # инструмент регистрируется только с URL
        await push_notify(self.settings.push_url, message, title)
        return {"sent": True}

    async def _tool_list_devices(self, params: dict[str, object]) -> dict[str, object]:
        devices: list[dict[str, object]] = []
        for device_id, rec in sorted(self.registry.all().items()):
            manifest = rec.manifest
            devices.append(
                {
                    "id": device_id,
                    "alias": manifest.alias,
                    "platform": manifest.platform,
                    "online": manifest.online,
                    "capabilities": [cap.name for cap in manifest.capabilities],
                }
            )
        return {"devices": devices}

    def _setup_device_tools(self) -> None:
        self.router.register_local(
            Capability(
                name="list_devices",
                description=(
                    "Список всех известных устройств: id, алиас, платформа, online, "
                    "возможности. Офлайн-устройства тоже видны (их можно разбудить)"
                ),
                risk=RiskLevel.safe,
            ),
            self._tool_list_devices,
        )
        self.router.register_local(
            Capability(
                name="wake_device",
                description=(
                    "Разбудить выключенное/спящее устройство по Wake-on-LAN "
                    "(params: device — алиас или id из list_devices)"
                ),
                risk=RiskLevel.confirm,
                params_schema={
                    "type": "object",
                    "properties": {"device": {"type": "string"}},
                    "required": ["device"],
                },
            ),
            self._tool_wake_device,
        )
        if self.settings.push_url:
            self.router.register_local(
                Capability(
                    name="notify_phone",
                    description=(
                        "Отправить push-уведомление на телефон пользователя "
                        "(params: message, title — опц.). Работает даже когда ПК выключен"
                    ),
                    risk=RiskLevel.safe,
                    params_schema={
                        "type": "object",
                        "properties": {
                            "message": {"type": "string"},
                            "title": {"type": "string"},
                        },
                        "required": ["message"],
                    },
                ),
                self._tool_notify_phone,
            )

    # --- долгосрочная память (Phase 5): remember / recall / forget ---

    async def _tool_remember(self, params: dict[str, object]) -> dict[str, object]:
        text = str(params.get("text", "")).strip()
        if not text:
            raise ValueError("нужен параметр text — сам факт")
        kind = str(params.get("kind", "fact")).strip() or "fact"
        if kind not in KINDS:
            raise ValueError(f"kind должен быть одним из: {', '.join(KINDS)}")
        fact_id = self.memory.remember(text, kind=kind)
        return {"remembered": True, "id": fact_id}

    async def _select_facts(self, query: str) -> list[Fact]:
        assert self._llm_client is not None  # инструменты регистрируются только с ключом
        return await select_relevant(
            self._llm_client,
            self.settings.llm_memory_model,
            self.memory.active_facts(),
            query,
        )

    async def _tool_recall(self, params: dict[str, object]) -> dict[str, object]:
        query = str(params.get("query", "")).strip()
        if not query:
            raise ValueError("нужен параметр query — что ищем в памяти")
        facts = await self._select_facts(query)
        if not facts:
            return {"facts": [], "note": "в памяти ничего подходящего не нашлось"}
        return {
            "facts": [{"text": f.text, "kind": f.kind, "when": f.created_at[:10]} for f in facts]
        }

    async def _tool_forget(self, params: dict[str, object]) -> dict[str, object]:
        query = str(params.get("query", "")).strip()
        if not query:
            raise ValueError("нужен параметр query — что забыть")
        facts = await self._select_facts(query)
        if not facts:
            return {"forgotten": [], "note": "в памяти ничего подходящего не нашлось"}
        self.memory.forget([f.id for f in facts])
        return {"forgotten": [f.text for f in facts]}

    def _setup_memory_tools(self) -> None:
        text_schema = {"type": "string"}
        self.router.register_local(
            Capability(
                name="remember",
                description=(
                    "Сохранить факт в долгосрочную память (params: text — сам факт, "
                    "сформулированный самодостаточно; kind: fact|preference|decision, опц.). "
                    "Используй, когда пользователь просит запомнить или сообщает что-то, "
                    "что пригодится в будущих разговорах"
                ),
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {
                        "text": text_schema,
                        "kind": {"type": "string", "enum": list(KINDS)},
                    },
                    "required": ["text"],
                },
            ),
            self._tool_remember,
        )
        self.router.register_local(
            Capability(
                name="recall",
                description=(
                    "Поискать в долгосрочной памяти (params: query). Используй, когда "
                    "пользователь спрашивает о прошлых фактах, предпочтениях или "
                    "договорённостях, которых нет в текущем диалоге"
                ),
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {"query": text_schema},
                    "required": ["query"],
                },
            ),
            self._tool_recall,
        )
        self.router.register_local(
            Capability(
                name="forget",
                description=(
                    "Забыть факты из долгосрочной памяти по описанию (params: query). "
                    "Перечисли пользователю, что именно забыто"
                ),
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {"query": text_schema},
                    "required": ["query"],
                },
            ),
            self._tool_forget,
        )

    def _setup_scheduler(self) -> None:
        self._scheduler = ActionScheduler(self.settings.scheduler_db, self._fire_scheduled)
        self._scheduler.start()
        obj_schema = {"type": "object"}
        self.router.register_local(
            Capability(
                name="schedule_action",
                description=(
                    "Отложенное действие: выполнить навык action через delay_seconds секунд "
                    "ИЛИ в момент at (ISO-время). action — имя навыка (например notify, "
                    "run_command) как в остальных инструментах. params — аргументы навыка. "
                    "target указывать НЕ нужно: устройство подбирается автоматически по навыку "
                    "(укажи только если нужно конкретное устройство по его id)"
                ),
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "params": obj_schema,
                        "delay_seconds": {"type": "integer"},
                        "at": {"type": "string"},
                        "target": {"type": "string"},
                    },
                    "required": ["action"],
                },
            ),
            self._tool_schedule_action,
        )
        self.router.register_local(
            Capability(
                name="schedule_cron",
                description=(
                    "Повторяющееся действие по cron-выражению (5 полей). Выполняет навык action "
                    "с params по расписанию. target указывать НЕ нужно — устройство подбирается "
                    "по навыку автоматически (укажи только для конкретного устройства по id)"
                ),
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "params": obj_schema,
                        "cron": {"type": "string"},
                        "target": {"type": "string"},
                    },
                    "required": ["action", "cron"],
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
            try:
                for device_id in self.registry.online_devices():
                    cmd = Command(source=CORE_ID, target=device_id, action="ping")
                    await self._bus_or_raise.publish_model(cmd_topic(device_id), cmd)
            except aiomqtt.MqttError:
                return  # связь упала — цикл сообщений это заметит и переподключится

    async def run(self) -> None:
        log.info(
            "Core стартует, брокер %s:%s (tls=%s, модель=%s)",
            self.settings.broker_host,
            self.settings.broker_port,
            self.settings.tls,
            self.settings.llm_model,
        )
        # Планировщик и аудит живут поверх переподключений — стартуем один раз.
        self._setup_scheduler()
        try:
            await run_with_reconnect(
                self._session,
                initial_delay=self.settings.reconnect_initial_delay,
                max_delay=self.settings.reconnect_max_delay,
            )
        finally:
            if self._scheduler is not None:
                self._scheduler.shutdown()
            self.registry.close()
            self.audit.close()
            self.memory.close()

    async def _session(self) -> None:
        """Один жизненный цикл соединения: connect → subscribe → цикл сообщений."""
        async with Bus(self.settings, client_id=CORE_ID) as bus:
            self._bus = bus
            await bus.subscribe(REGISTRY_WILDCARD)
            await bus.subscribe(RESP_WILDCARD)
            await bus.subscribe(USER_REQUEST)
            await bus.subscribe(USER_CONFIRM)
            await bus.subscribe(EVENT_WILDCARD)
            log.info(
                "Core подключён, слушаю registry + responses + запросы + подтверждения + события"
            )

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
                    elif topic.startswith(f"{PREFIX}/event/"):
                        self._spawn_event(data)
            finally:
                pinger.cancel()


async def run() -> None:
    load_env()
    setup_logging()
    await Core(BusSettings()).run()


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        aio.run(run())


if __name__ == "__main__":
    main()
