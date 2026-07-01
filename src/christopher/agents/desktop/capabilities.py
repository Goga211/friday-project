"""Возможности desktop-агента.

Каждая возможность = (Capability с уровнем риска, async-обработчик). Безопасные (ping,
system_info, notify, open_url, screenshot) исполняются сразу; risky (launch_app, type_text —
confirm; run_command — dangerous) — только после подтверждения (см. флоу подтверждения в Core).
Реализация навыков управления — в skills.py (диспатч по ОС).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from christopher.agents.desktop import skills
from christopher.shared.protocol import Capability, RiskLevel

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_STRING = {"type": "string"}


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
    "open_url": (
        Capability(
            name="open_url",
            description="Открыть URL в браузере по умолчанию (params: url — http/https)",
            risk=RiskLevel.safe,
            params_schema={
                "type": "object",
                "properties": {"url": _STRING},
                "required": ["url"],
            },
        ),
        skills.open_url,
    ),
    "screenshot": (
        Capability(
            name="screenshot",
            description="Сделать скриншот экрана, вернуть путь к PNG (params: path — опц.)",
            risk=RiskLevel.safe,
            params_schema={"type": "object", "properties": {"path": _STRING}},
        ),
        skills.screenshot,
    ),
    "launch_app": (
        Capability(
            name="launch_app",
            description="Запустить приложение по имени из PATH (params: name, args — опц. список)",
            risk=RiskLevel.confirm,
            params_schema={
                "type": "object",
                "properties": {
                    "name": _STRING,
                    "args": {"type": "array", "items": _STRING},
                },
                "required": ["name"],
            },
        ),
        skills.launch_app,
    ),
    "type_text": (
        Capability(
            name="type_text",
            description=(
                "Напечатать текст в активное окно (params: text). "
                "На Wayland+GNOME может быть заблокировано"
            ),
            risk=RiskLevel.confirm,
            params_schema={
                "type": "object",
                "properties": {"text": _STRING},
                "required": ["text"],
            },
        ),
        skills.type_text,
    ),
    "run_command": (
        Capability(
            name="run_command",
            description=(
                "Выполнить shell-команду из allowlist (params: command). "
                "Только разрешённые программы"
            ),
            risk=RiskLevel.dangerous,
            params_schema={
                "type": "object",
                "properties": {"command": _STRING},
                "required": ["command"],
            },
        ),
        skills.run_command,
    ),
}


def manifest_capabilities() -> list[Capability]:
    return [cap for cap, _ in REGISTRY.values()]
