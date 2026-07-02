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
    def _tool_def(cap: Capability) -> dict[str, Any]:
        schema = dict(cap.params_schema) if cap.params_schema else dict(_EMPTY_SCHEMA)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        description = cap.description
        if cap.risk is not RiskLevel.safe:
            description += f" (риск: {cap.risk.value}, требует подтверждения)"
        return {"name": cap.name, "description": description, "input_schema": schema}

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Инструменты для Claude: локальные (Core) + возможности онлайн-устройств (без дублей).

        Отсортированы по имени: prompt caching — префиксный, недетерминированный порядок
        инструментов молча инвалидировал бы кэш system+tools на каждом запросе.
        """
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        for cap, _handler in self._local.values():
            seen.add(cap.name)
            tools.append(self._tool_def(cap))
        for rec in self._registry.all().values():
            if not rec.manifest.online:
                continue
            for cap in rec.manifest.capabilities:
                if cap.name in seen:
                    continue
                seen.add(cap.name)
                tools.append(self._tool_def(cap))
        tools.sort(key=lambda t: str(t["name"]))
        return tools

    def _find_device(self, action: str) -> tuple[str | None, Capability | None]:
        for device_id, rec in self._registry.all().items():
            if not rec.manifest.online:
                continue
            for cap in rec.manifest.capabilities:
                if cap.name == action:
                    return device_id, cap
        return None, None

    def resolve_target(self, hint: str | None, action: str) -> str | None:
        """Найти онлайн-устройство для действия (для отложенного запуска планировщиком).

        Сначала пробуем hint (если это реальное онлайн-устройство с нужной возможностью —
        мозг мог указать конкретное устройство). Иначе резолвим по возможности, как при
        немедленном вызове. None — если подходящего онлайн-устройства нет.
        """
        if hint:
            rec = self._registry.all().get(hint)
            if (
                rec is not None
                and rec.manifest.online
                and any(cap.name == action for cap in rec.manifest.capabilities)
            ):
                return hint
        device_id, _cap = self._find_device(action)
        return device_id

    @staticmethod
    def _summary(action: str, params: dict[str, Any]) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in params.items())
        return f"{action}({args})" if args else f"{action}()"

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
                summary=self._summary(action, params),
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
        """
        local = self._local.get(action)
        if local is not None:
            local_cap, handler = local
            if local_cap.risk is not RiskLevel.safe and pending is not None:
                return self._defer(pending, _LOCAL_DEVICE, action, params, local_cap.risk)
            return await self._run_local(action, handler, params)

        device_id, cap = self._find_device(action)
        if device_id is None or cap is None:
            return {"ok": False, "error": f"нет онлайн-устройства с возможностью '{action}'"}
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
