"""Маршрутизатор инструментов: мост между Claude (tool-use) и устройствами на шине.

- строит определения инструментов из возможностей онлайн-устройств (для Claude);
- выполняет вызов инструмента: находит устройство, шлёт команду, ждёт ответ, пишет аудит.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from christopher.core.audit import AuditLog
from christopher.core.registry import DeviceRegistry
from christopher.shared.protocol import Capability, PendingAction, Response, RiskLevel

log = logging.getLogger("christopher.router")

# (device_id, action, params, requires_confirm) -> Response
DeviceCaller = Callable[[str, str, dict[str, Any], bool], Awaitable[Response]]

_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


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

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Инструменты для Claude из возможностей всех онлайн-устройств (без дублей по имени)."""
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        for rec in self._registry.all().values():
            if not rec.manifest.online:
                continue
            for cap in rec.manifest.capabilities:
                if cap.name in seen:
                    continue
                seen.add(cap.name)
                schema = dict(cap.params_schema) if cap.params_schema else dict(_EMPTY_SCHEMA)
                schema.setdefault("type", "object")
                schema.setdefault("properties", {})
                description = cap.description
                if cap.risk is not RiskLevel.safe:
                    description += f" (риск: {cap.risk.value}, требует подтверждения)"
                tools.append({"name": cap.name, "description": description, "input_schema": schema})
        return tools

    def _find_device(self, action: str) -> tuple[str | None, Capability | None]:
        for device_id, rec in self._registry.all().items():
            if not rec.manifest.online:
                continue
            for cap in rec.manifest.capabilities:
                if cap.name == action:
                    return device_id, cap
        return None, None

    @staticmethod
    def _summary(action: str, params: dict[str, Any]) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in params.items())
        return f"{action}({args})" if args else f"{action}()"

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
        device_id, cap = self._find_device(action)
        if device_id is None:
            return {"ok": False, "error": f"нет онлайн-устройства с возможностью '{action}'"}

        if cap is not None and cap.risk is not RiskLevel.safe and pending is not None:
            pending.append(
                PendingAction(
                    device_id=device_id,
                    action=action,
                    params=params,
                    risk=cap.risk,
                    summary=self._summary(action, params),
                )
            )
            return {
                "ok": True,
                "status": "confirmation_required",
                "message": (
                    f"Действие '{action}' уровня {cap.risk.value} требует подтверждения "
                    "пользователя. Сообщи ему, что нужно подтвердить, и НЕ считай действие "
                    "выполненным."
                ),
            }

        return await self._call_and_audit(device_id, action, params, requires_confirm=False)

    async def execute_confirmed(self, pa: PendingAction) -> dict[str, Any]:
        """Выполнить подтверждённое пользователем risky-действие (requires_confirm=True)."""
        return await self._call_and_audit(
            pa.device_id, pa.action, dict(pa.params), requires_confirm=True
        )

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
