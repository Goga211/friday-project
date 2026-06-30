"""Возможности desktop-агента (Phase 0 — только безопасные read/notify).

Каждая возможность = (Capability с уровнем риска, async-обработчик). Опасные действия
(run_command, launch_app, power и т.п.) добавляются в Phase 1 с уровнями confirm/dangerous.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from christopher.shared.protocol import Capability, RiskLevel

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


async def _ping(params: dict[str, Any]) -> dict[str, Any]:
    return {"pong": True}


async def _system_info(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "hostname": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }


async def _notify(params: dict[str, Any]) -> dict[str, Any]:
    title = str(params.get("title", "Christopher"))
    message = str(params.get("message", ""))
    notifier = shutil.which("notify-send")
    if notifier is None:
        raise RuntimeError("notify-send недоступен (поставь libnotify-bin)")
    proc = await asyncio.create_subprocess_exec(notifier, title, message)
    await proc.wait()
    return {"sent": True}


REGISTRY: dict[str, tuple[Capability, Handler]] = {
    "ping": (
        Capability(name="ping", description="Проверка живости агента", risk=RiskLevel.safe),
        _ping,
    ),
    "system_info": (
        Capability(name="system_info", description="Информация о системе", risk=RiskLevel.safe),
        _system_info,
    ),
    "notify": (
        Capability(
            name="notify",
            description="Показать desktop-уведомление (params: title, message)",
            risk=RiskLevel.safe,
            params_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        ),
        _notify,
    ),
}


def manifest_capabilities() -> list[Capability]:
    return [cap for cap, _ in REGISTRY.values()]
