"""Реальные навыки desktop-агента с диспатчем по ОС.

Windows — приоритетная среда управления (Win32 через winctl.py, ctypes без зависимостей);
Linux — best-effort (X11 надёжно, Wayland+GNOME блокирует ввод/окна); macOS — заглушки
с понятной ошибкой. Уровни риска и allowlist — по §4 плана; risky-навыки исполняются
только после подтверждения (флаг requires_confirm на команде).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

from friday.agents.desktop import winctl

_SYSTEM = platform.system()  # 'Linux' | 'Windows' | 'Darwin'

# Дефолтный allowlist для run_command (можно расширить FRIDAY_CMD_ALLOWLIST="a,b,c").
_ALLOWLIST_POSIX = (
    "ls", "cat", "echo", "pwd", "whoami", "date", "uptime",
    "df", "free", "uname", "ps", "which", "env", "hostname",
)  # fmt: skip
_ALLOWLIST_WINDOWS = (
    "tasklist", "ipconfig", "systeminfo", "hostname", "whoami", "where", "echo",
)  # fmt: skip

_CMD_TIMEOUT = 15.0
_WINDOW_ACTIONS = ("minimize", "maximize", "restore", "close")


def _allowlist() -> set[str]:
    raw = os.getenv("FRIDAY_CMD_ALLOWLIST")
    if raw:
        return {c.strip() for c in raw.split(",") if c.strip()}
    default = _ALLOWLIST_WINDOWS if _SYSTEM == "Windows" else _ALLOWLIST_POSIX
    return set(default)


def _unsupported(skill: str) -> RuntimeError:
    return RuntimeError(f"навык '{skill}' не реализован на этой ОС ({_SYSTEM})")


def _require_x11(skill: str) -> None:
    """Wayland (особенно GNOME) блокирует инжект ввода и управление окнами извне."""
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        raise RuntimeError(
            f"навык '{skill}': сессия Wayland — управление окнами заблокировано "
            "(работает только X11/XWayland)"
        )


def _which_first(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


async def spawn_detached(*argv: str) -> None:
    """Запустить и забыть (GUI-приложение/браузер), не блокируя агента."""
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        await asyncio.create_subprocess_exec(
            *argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags
        )
        return
    await asyncio.create_subprocess_exec(
        *argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )


async def _run_checked(argv: list[str], *, error_prefix: str) -> None:
    """Выполнить утилиту, поднять RuntimeError с её stderr при ненулевом коде выхода."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{error_prefix}: {stderr.decode(errors='replace')[:200]}")


# --- запуск и URL ---


async def open_url(params: dict[str, Any]) -> dict[str, Any]:
    url = str(params.get("url", "")).strip()
    if not url:
        raise ValueError("нужен параметр url")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("url должен начинаться с http:// или https://")

    if sys.platform == "win32":  # inline-проверка — ради сужения типов mypy
        os.startfile(url)  # noqa: S606 — открытие URL дефолтным браузером и есть цель
        return {"opened": url}
    if _SYSTEM == "Linux":
        opener = shutil.which("xdg-open")
        if opener is None:
            raise RuntimeError("xdg-open недоступен (поставь xdg-utils)")
        await spawn_detached(opener, url)
        return {"opened": url}
    raise _unsupported("open_url")


async def launch_app(params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get("name", "")).strip()
    if not name:
        raise ValueError("нужен параметр name")
    if _SYSTEM not in ("Linux", "Windows"):
        raise _unsupported("launch_app")
    exe = shutil.which(name)  # на Windows найдёт и .exe/.bat через PATHEXT
    if exe is None:
        raise RuntimeError(f"приложение '{name}' не найдено в PATH")
    raw_args = params.get("args") or []
    if not isinstance(raw_args, list):
        raise ValueError("args должен быть списком")
    await spawn_detached(exe, *[str(a) for a in raw_args])
    return {"launched": name}


async def run_command(params: dict[str, Any]) -> dict[str, Any]:
    command = str(params.get("command", "")).strip()
    if not command:
        raise ValueError("нужен параметр command")
    try:
        argv = shlex.split(command, posix=_SYSTEM != "Windows")
    except ValueError as exc:
        raise ValueError(f"не удалось разобрать команду: {exc}") from exc
    if not argv:
        raise ValueError("пустая команда")

    prog = os.path.basename(argv[0])
    if _SYSTEM == "Windows":
        prog = prog.removesuffix(".exe")
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


# --- ввод и экран ---


async def type_text(params: dict[str, Any]) -> dict[str, Any]:
    text = str(params.get("text", ""))
    if not text:
        raise ValueError("нужен параметр text")

    if _SYSTEM == "Windows":
        typed = await asyncio.to_thread(winctl.send_text, text)
        return {"typed_chars": typed, "tool": "win32"}

    if _SYSTEM != "Linux":
        raise _unsupported("type_text")
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
    await _run_checked(argv, error_prefix=f"{base} ошибка")
    return {"typed_chars": len(text), "tool": base}


_WINDOWS_SCREENSHOT_PS = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size)
$bitmap.Save({path}, [System.Drawing.Imaging.ImageFormat]::Png)
"""


def ps_quote(value: str) -> str:
    """Строковый литерал PowerShell в одинарных кавычках (' экранируется удвоением)."""
    return "'" + value.replace("'", "''") + "'"


async def screenshot(params: dict[str, Any]) -> dict[str, Any]:
    path = str(params.get("path", "")).strip()
    if not path:
        path = os.path.join(tempfile.gettempdir(), f"friday-shot-{int(time.time())}.png")

    if _SYSTEM == "Windows":
        script = _WINDOWS_SCREENSHOT_PS.format(path=ps_quote(path))
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
        await _run_checked(argv, error_prefix="powershell не сделал скриншот")
        if not os.path.exists(path):
            raise RuntimeError("powershell отработал, но файл скриншота не появился")
        return {"path": path, "tool": "powershell"}

    if _SYSTEM != "Linux":
        raise _unsupported("screenshot")
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
    await _run_checked(argv, error_prefix=f"{base} не сделал скриншот")
    if not os.path.exists(path):
        raise RuntimeError(f"{base} отработал, но файл скриншота не появился")
    return {"path": path, "tool": base}


# --- управление окнами (Windows — Win32; Linux X11 — wmctrl best-effort) ---


def _wmctrl() -> str:
    tool = shutil.which("wmctrl")
    if tool is None:
        raise RuntimeError("wmctrl недоступен (поставь wmctrl; работает только на X11)")
    return tool


async def list_windows(params: dict[str, Any]) -> dict[str, Any]:
    if _SYSTEM == "Windows":
        windows = await asyncio.to_thread(winctl.list_windows)
        return {"windows": [w["title"] for w in windows]}

    if _SYSTEM != "Linux":
        raise _unsupported("list_windows")
    _require_x11("list_windows")
    proc = await asyncio.create_subprocess_exec(
        _wmctrl(), "-l", stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"wmctrl -l: {stderr.decode(errors='replace')[:200]}")
    titles: list[str] = []
    for line in stdout.decode(errors="replace").splitlines():
        parts = line.split(None, 3)  # id, desktop, host, title
        if len(parts) == 4 and parts[3].strip():
            titles.append(parts[3].strip())
    return {"windows": titles}


async def focus_window(params: dict[str, Any]) -> dict[str, Any]:
    title = str(params.get("title", "")).strip()
    if not title:
        raise ValueError("нужен параметр title (подстрока заголовка окна)")

    if _SYSTEM == "Windows":
        win = await asyncio.to_thread(winctl.focus_window, title)
        return {"focused": win["title"]}

    if _SYSTEM != "Linux":
        raise _unsupported("focus_window")
    _require_x11("focus_window")
    await _run_checked([_wmctrl(), "-a", title], error_prefix="wmctrl -a")
    return {"focused": title}


async def manage_window(params: dict[str, Any]) -> dict[str, Any]:
    title = str(params.get("title", "")).strip()
    action = str(params.get("action", "")).strip().lower()
    if not title:
        raise ValueError("нужен параметр title (подстрока заголовка окна)")
    if action not in _WINDOW_ACTIONS:
        raise ValueError(f"action должен быть одним из: {', '.join(_WINDOW_ACTIONS)}")

    if _SYSTEM == "Windows":
        win = await asyncio.to_thread(winctl.manage_window, title, action)
        return {"window": win["title"], "action": action}

    if _SYSTEM != "Linux":
        raise _unsupported("manage_window")
    _require_x11("manage_window")
    tool = _wmctrl()
    if action == "close":
        commands = [[tool, "-c", title]]
    elif action == "minimize":
        commands = [[tool, "-r", title, "-b", "add,hidden"]]
    elif action == "maximize":
        commands = [[tool, "-r", title, "-b", "add,maximized_vert,maximized_horz"]]
    else:  # restore — wmctrl -b принимает максимум 2 свойства за вызов
        commands = [
            [tool, "-r", title, "-b", "remove,hidden"],
            [tool, "-r", title, "-b", "remove,maximized_vert,maximized_horz"],
        ]
    for argv in commands:
        await _run_checked(argv, error_prefix=f"wmctrl ({action})")
    return {"window": title, "action": action}
