"""Возможности desktop-агента.

Каждая возможность = (Capability с уровнем риска, async-обработчик). Безопасные (ping,
system_info, notify, open_url, screenshot) исполняются сразу; risky (launch_app, type_text —
confirm; run_command — dangerous) — только после подтверждения (см. флоу подтверждения в Core).
Реализация навыков управления — в skills.py (диспатч по ОС).
"""

from __future__ import annotations

import os
import platform
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from friday.agents.desktop import claude_code, skills
from friday.shared import proc
from friday.shared.protocol import Capability, RiskLevel

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


_WINDOWS_NOTIFY_PS = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.Visible = $true
$icon.ShowBalloonTip(5000, {title}, {message}, 'Info')
Start-Sleep -Seconds 6
$icon.Dispose()
"""


async def _notify(params: dict[str, Any]) -> dict[str, Any]:
    title = str(params.get("title", "Пятница"))
    message = str(params.get("message", ""))

    if platform.system() == "Windows":
        script = _WINDOWS_NOTIFY_PS.format(
            title=skills.ps_quote(title), message=skills.ps_quote(message or " ")
        )
        # fire-and-forget: скрипт сам живёт ~6 с, пока показывается баллон
        await skills.spawn_detached(
            "powershell", "-NoProfile", "-NonInteractive", "-Command", script
        )
        return {"sent": True}

    notifier = shutil.which("notify-send")
    if notifier is None:
        raise RuntimeError("notify-send недоступен (поставь libnotify-bin)")
    await proc.run(notifier, title, message)
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
    "list_windows": (
        Capability(
            name="list_windows",
            description="Список заголовков открытых окон (Windows/X11)",
            risk=RiskLevel.safe,
        ),
        skills.list_windows,
    ),
    "focus_window": (
        Capability(
            name="focus_window",
            description=(
                "Вывести окно на передний план по подстроке заголовка "
                "(params: title; Windows/X11)"
            ),
            risk=RiskLevel.confirm,
            params_schema={
                "type": "object",
                "properties": {"title": _STRING},
                "required": ["title"],
            },
        ),
        skills.focus_window,
    ),
    "manage_window": (
        Capability(
            name="manage_window",
            description=(
                "Свернуть/развернуть/восстановить/закрыть окно по подстроке заголовка "
                "(params: title, action: minimize|maximize|restore|close; Windows/X11)"
            ),
            risk=RiskLevel.confirm,
            params_schema={
                "type": "object",
                "properties": {
                    "title": _STRING,
                    "action": {
                        "type": "string",
                        "enum": ["minimize", "maximize", "restore", "close"],
                    },
                },
                "required": ["title", "action"],
            },
        ),
        skills.manage_window,
    ),
    "power": (
        Capability(
            name="power",
            description=(
                "Управление питанием: усыпить/выключить/перезагрузить эту машину "
                "(params: action: sleep|shutdown|reboot)"
            ),
            risk=RiskLevel.dangerous,
            params_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["sleep", "shutdown", "reboot"],
                    },
                },
                "required": ["action"],
            },
        ),
        skills.power,
    ),
    "lock_screen": (
        Capability(
            name="lock_screen",
            description="Заблокировать экран",
            risk=RiskLevel.confirm,
        ),
        skills.lock_screen,
    ),
    "run_claude_task": (
        Capability(
            name="run_claude_task",
            description=(
                "Делегировать сложную многошаговую задачу (код, файлы, длинные сценарии) "
                "Claude Code на этой машине. params: task — самодостаточное ТЗ со всем "
                "контекстом (Claude Code не видит этот диалог); cwd — рабочая папка, опц.; "
                "mode: auto|visible|headless, опц. Задача идёт минуты; в headless результат "
                "придёт позже отдельным сообщением"
            ),
            risk=RiskLevel.confirm,
            params_schema={
                "type": "object",
                "properties": {
                    "task": _STRING,
                    "cwd": _STRING,
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "visible", "headless"],
                    },
                },
                "required": ["task"],
            },
        ),
        claude_code.run_claude_task,
    ),
}


def manifest_capabilities() -> list[Capability]:
    return [cap for cap, _ in REGISTRY.values()]
