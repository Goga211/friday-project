"""Маршрутизатор инструментов: мост между Claude (tool-use) и устройствами на шине.

- строит определения инструментов из возможностей онлайн-устройств (для Claude);
- выполняет вызов инструмента: находит устройство, шлёт команду, ждёт ответ, пишет аудит.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from friday.core.audit import AuditLog
from friday.core.registry import DeviceRegistry
from friday.shared.protocol import Capability, PendingAction, Response, RiskLevel

log = logging.getLogger("friday.router")

# (device_id, action, params, requires_confirm) -> Response
DeviceCaller = Callable[[str, str, dict[str, Any], bool], Awaitable[Response]]
# Локальный инструмент, исполняемый в самом Core (напр. планировщик) — без выхода на шину.
LocalHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}
_LOCAL_DEVICE = "core"


class ToolRouter:
    def __init__(
        self,
        registry: DeviceRegistry,
        caller: DeviceCaller,
        audit: AuditLog | None = None,
    ) -> None:
        self._registry = registry
        self._caller = caller
        self._audit = audit
        self._local: dict[str, tuple[Capability, LocalHandler]] = {}

    def register_local(self, capability: Capability, handler: LocalHandler) -> None:
        """Зарегистрировать инструмент, исполняемый локально в Core (напр. планировщик)."""
        self._local[capability.name] = (capability, handler)

    @staticmethod
    def _tool_def(cap: Capability, device_labels: list[str] | None = None) -> dict[str, Any]:
        # копируем и вложенные properties: инъекция device не должна мутировать Capability
        schema = dict(cap.params_schema) if cap.params_schema else dict(_EMPTY_SCHEMA)
        schema.setdefault("type", "object")
        schema["properties"] = dict(schema.get("properties") or {})
        description = cap.description
        if cap.risk is not RiskLevel.safe:
            description += f" (риск: {cap.risk.value}, требует подтверждения)"
        if device_labels is not None:
            schema["properties"]["device"] = {
                "type": "string",
                "description": (
                    "Целевое устройство: алиас или id. Не указывай — выберется автоматически"
                ),
            }
            if len(device_labels) > 1:
                description += f" (есть на устройствах: {', '.join(device_labels)})"
        return {"name": cap.name, "description": description, "input_schema": schema}

    def _label(self, device_id: str) -> str:
        rec = self._registry.get(device_id)
        if rec is not None and rec.manifest.alias:
            return rec.manifest.alias
        return device_id

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Инструменты для Claude: локальные (Core) + возможности онлайн-устройств (без дублей).

        Возможность на нескольких устройствах — один инструмент с параметром device
        (алиасы перечислены в описании). Отсортированы по имени: prompt caching —
        префиксный, недетерминированный порядок инструментов молча инвалидировал бы
        кэш system+tools на каждом запросе.
        """
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        for cap, _handler in self._local.values():
            seen.add(cap.name)
            tools.append(self._tool_def(cap))
        # какие онлайн-устройства предоставляют каждую возможность (в стабильном порядке)
        providers: dict[str, list[str]] = {}
        first_cap: dict[str, Capability] = {}
        for device_id, rec in sorted(self._registry.all().items()):
            if not rec.manifest.online:
                continue
            for cap in rec.manifest.capabilities:
                if cap.name in seen:
                    continue
                providers.setdefault(cap.name, []).append(self._label(device_id))
                first_cap.setdefault(cap.name, cap)
        for name, cap in first_cap.items():
            tools.append(self._tool_def(cap, device_labels=providers[name]))
        tools.sort(key=lambda t: str(t["name"]))
        return tools

    def _find_device(
        self, action: str, target: str | None = None
    ) -> tuple[str | None, Capability | None, str | None]:
        """Подобрать устройство для действия: (device_id, capability, ошибка).

        target (алиас или id) — явное указание пользователя/мозга; без него берём первое
        онлайн-устройство с нужной возможностью.
        """
        if target:
            rec = self._registry.resolve(target)
            if rec is None:
                return None, None, f"неизвестное устройство '{target}' (см. list_devices)"
            label = rec.manifest.alias or rec.manifest.device_id
            cap = next((c for c in rec.manifest.capabilities if c.name == action), None)
            if cap is None:
                return None, None, f"устройство '{label}' не умеет '{action}'"
            if not rec.manifest.online:
                return (
                    None,
                    None,
                    (f"устройство '{label}' офлайн — его можно разбудить через wake_device"),
                )
            return rec.manifest.device_id, cap, None
        for device_id, rec in self._registry.all().items():
            if not rec.manifest.online:
                continue
            for cap in rec.manifest.capabilities:
                if cap.name == action:
                    return device_id, cap, None
        return None, None, f"нет онлайн-устройства с возможностью '{action}'"

    def resolve_target(self, hint: str | None, action: str) -> str | None:
        """Найти онлайн-устройство для действия (для отложенного запуска планировщиком).

        Сначала пробуем hint (алиас или id — мозг мог указать конкретное устройство).
        Иначе резолвим по возможности, как при немедленном вызове. None — если
        подходящего онлайн-устройства нет.
        """
        if hint:
            rec = self._registry.resolve(hint)
            if (
                rec is not None
                and rec.manifest.online
                and any(cap.name == action for cap in rec.manifest.capabilities)
            ):
                return rec.manifest.device_id
        device_id, _cap, _err = self._find_device(action)
        return device_id

    def _summary(self, device_id: str, action: str, params: dict[str, Any]) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in params.items())
        call = f"{action}({args})" if args else f"{action}()"
        if device_id == _LOCAL_DEVICE:
            return call
        return f"{call} на «{self._label(device_id)}»"

    def _defer(
        self,
        pending: list[PendingAction],
        device_id: str,
        action: str,
        params: dict[str, Any],
        risk: RiskLevel,
    ) -> dict[str, Any]:
        pending.append(
            PendingAction(
                device_id=device_id,
                action=action,
                params=params,
                risk=risk,
                summary=self._summary(device_id, action, params),
            )
        )
        return {
            "ok": True,
            "status": "confirmation_required",
            "message": (
                f"Действие '{action}' уровня {risk.value} требует подтверждения пользователя. "
                "Сообщи ему, что нужно подтвердить, и НЕ считай действие выполненным."
            ),
        }

    async def execute(
        self,
        action: str,
        params: dict[str, Any],
        pending: list[PendingAction] | None = None,
    ) -> dict[str, Any]:
        """Выполнить safe-действие сразу; risky — отложить в pending на подтверждение.

        Если pending-коллектор передан и действие не safe — действие НЕ выполняется, а
        кладётся в pending; мозг сообщает об этом пользователю. Подтверждённое действие
        затем выполняет execute_confirmed().

        Параметр device в params (инъектирован в схемы device-backed инструментов) —
        целевое устройство (алиас или id); извлекается здесь и до навыка не доходит.
        Для локальных инструментов params не трогаем: например, у wake_device параметр
        device — его собственный аргумент.
        """
        local = self._local.get(action)
        if local is not None:
            local_cap, handler = local
            if local_cap.risk is not RiskLevel.safe and pending is not None:
                return self._defer(pending, _LOCAL_DEVICE, action, params, local_cap.risk)
            return await self._run_local(action, handler, params)

        params = dict(params)
        raw_target = params.pop("device", None)
        target = str(raw_target).strip() if raw_target else None
        device_id, cap, error = self._find_device(action, target)
        if device_id is None or cap is None:
            return {"ok": False, "error": error}
        if cap.risk is not RiskLevel.safe and pending is not None:
            return self._defer(pending, device_id, action, params, cap.risk)
        return await self._call_and_audit(device_id, action, params, requires_confirm=False)

    async def execute_confirmed(self, pa: PendingAction) -> dict[str, Any]:
        """Выполнить подтверждённое пользователем risky-действие (requires_confirm=True)."""
        local = self._local.get(pa.action)
        if local is not None:
            return await self._run_local(pa.action, local[1], dict(pa.params))
        return await self._call_and_audit(
            pa.device_id, pa.action, dict(pa.params), requires_confirm=True
        )

    async def _run_local(
        self, action: str, handler: LocalHandler, params: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            result = await handler(params)
            out: dict[str, Any] = {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001 — сбой локального инструмента не роняет мозг
            log.warning("сбой локального инструмента %s: %s", action, exc)
            out = {"ok": False, "error": str(exc)}
        if self._audit is not None:
            self._audit.record(
                device=_LOCAL_DEVICE,
                action=action,
                params=params,
                ok=bool(out["ok"]),
                error=out.get("error"),
            )
        return out

    async def _call_and_audit(
        self, device_id: str, action: str, params: dict[str, Any], *, requires_confirm: bool
    ) -> dict[str, Any]:
        try:
            resp = await self._caller(device_id, action, params, requires_confirm)
            out: dict[str, Any] = {"ok": resp.ok, "result": resp.result, "error": resp.error}
        except Exception as exc:  # noqa: BLE001 — сбой связи не должен ронять мозг
            log.warning("сбой вызова %s на %s: %s", action, device_id, exc)
            out = {"ok": False, "error": f"сбой вызова устройства: {exc}"}

        if self._audit is not None:
            self._audit.record(
                device=device_id,
                action=action,
                params=params,
                ok=bool(out["ok"]),
                error=out.get("error"),
            )
        return out
