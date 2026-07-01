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
from christopher.shared.protocol import Capability, Response, RiskLevel

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

    async def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        device_id, _cap = self._find_device(action)
        if device_id is None:
            return {"ok": False, "error": f"нет онлайн-устройства с возможностью '{action}'"}

        # Пока не авто-подтверждаем risky-действия: агент отклонит без requires_confirm.
        # Полный флоу подтверждения (спросить пользователя) — следующий срез.
        requires_confirm = False
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
