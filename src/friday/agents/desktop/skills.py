"""Реальные навыки desktop-агента с диспатчем по ОС (Phase 1 срез 2).

Пока реализован Linux (best-effort под Wayland/X11); Windows/macOS — заглушки с понятной
ошибкой (кросс-платформенный агент, реализации подтягиваются по ОС за общим интерфейсом
навыка). Уровни риска и allowlist — по §4 плана; risky-навыки исполняются только после
подтверждения (флаг requires_confirm на команде).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Any

_SYSTEM = platform.system()  # 'Linux' | 'Windows' | 'Darwin'

# Дефолтный allowlist для run_command (можно расширить FRIDAY_CMD_ALLOWLIST="a,b,c").
_DEFAULT_ALLOWLIST = (
    "ls", "cat", "echo", "pwd", "whoami", "date", "uptime",
    "df", "free", "uname", "ps", "which", "env", "hostname",
)  # fmt: skip

_CMD_TIMEOUT = 15.0


def _allowlist() -> set[str]:
    raw = os.getenv("FRIDAY_CMD_ALLOWLIST")
    if raw:
        return {c.strip() for c in raw.split(",") if c.strip()}
    return set(_DEFAULT_ALLOWLIST)


def _require_linux(skill: str) -> None:
    if _SYSTEM != "Linux":
        raise RuntimeError(
            f"навык '{skill}' пока реализован только на Linux (текущая ОС: {_SYSTEM})"
        )


def _which_first(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


async def _spawn_detached(*argv: str) -> None:
    """Запустить и забыть (GUI-приложение/браузер), не блокируя агента."""
    await asyncio.create_subprocess_exec(
        *argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )


async def open_url(params: dict[str, Any]) -> dict[str, Any]:
    url = str(params.get("url", "")).strip()
    if not url:
        raise ValueError("нужен параметр url")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("url должен начинаться с http:// или https://")
    _require_linux("open_url")
    opener = shutil.which("xdg-open")
    if opener is None:
        raise RuntimeError("xdg-open недоступен (поставь xdg-utils)")
    await _spawn_detached(opener, url)
    return {"opened": url}


async def launch_app(params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get("name", "")).strip()
    if not name:
        raise ValueError("нужен параметр name")
    _require_linux("launch_app")
    exe = shutil.which(name)
    if exe is None:
        raise RuntimeError(f"приложение '{name}' не найдено в PATH")
    raw_args = params.get("args") or []
    if not isinstance(raw_args, list):
        raise ValueError("args должен быть списком")
    await _spawn_detached(exe, *[str(a) for a in raw_args])
    return {"launched": name}


async def run_command(params: dict[str, Any]) -> dict[str, Any]:
    command = str(params.get("command", "")).strip()
    if not command:
        raise ValueError("нужен параметр command")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"не удалось разобрать команду: {exc}") from exc
    if not argv:
        raise ValueError("пустая команда")

    prog = os.path.basename(argv[0])
    allow = _allowlist()
    if prog not in allow:
        raise PermissionError(f"команда '{prog}' не в allowlist ({', '.join(sorted(allow))})")
    exe = shutil.which(argv[0])
    if exe is None:
        raise RuntimeError(f"'{argv[0]}' не найден в PATH")

    proc = await asyncio.create_subprocess_exec(
        exe, *argv[1:], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_CMD_TIMEOUT)
    except TimeoutError as exc:
        proc.kill()
        raise RuntimeError(f"команда превысила таймаут {_CMD_TIMEOUT:.0f}с") from exc
    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode(errors="replace")[:4000],
        "stderr": stderr.decode(errors="replace")[:2000],
    }


async def type_text(params: dict[str, Any]) -> dict[str, Any]:
    text = str(params.get("text", ""))
    if not text:
        raise ValueError("нужен параметр text")
    _require_linux("type_text")
    tool = _which_first("wtype", "ydotool", "xdotool")
    if tool is None:
        raise RuntimeError(
            "нет инструмента ввода (wtype/ydotool/xdotool); на Wayland+GNOME инжект ввода "
            "может быть заблокирован"
        )
    base = os.path.basename(tool)
    if base == "wtype":
        argv = [tool, text]
    elif base == "ydotool":
        argv = [tool, "type", text]
    else:  # xdotool
        argv = [tool, "type", "--", text]

    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{base} ошибка: {stderr.decode(errors='replace')[:200]}")
    return {"typed_chars": len(text), "tool": base}


async def screenshot(params: dict[str, Any]) -> dict[str, Any]:
    _require_linux("screenshot")
    path = str(params.get("path", "")).strip()
    if not path:
        path = os.path.join(tempfile.gettempdir(), f"friday-shot-{int(time.time())}.png")
    tool = _which_first("grim", "gnome-screenshot", "spectacle", "scrot")
    if tool is None:
        raise RuntimeError("нет утилиты скриншота (grim/gnome-screenshot/spectacle/scrot)")
    base = os.path.basename(tool)
    if base == "gnome-screenshot":
        argv = [tool, "-f", path]
    elif base == "spectacle":
        argv = [tool, "-b", "-n", "-o", path]
    else:  # grim / scrot — путь последним аргументом
        argv = [tool, path]

    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(path):
        raise RuntimeError(f"{base} не сделал скриншот: {stderr.decode(errors='replace')[:200]}")
    return {"path": path, "tool": base}
