"""Делегирование сложных задач в Claude Code на этой машине.

Мозг Пятницы — диспетчер: многошаговую работу с кодом/файлами/приложениями он отдаёт
локальному Claude Code (подписка пользователя вместо API-токенов). Два режима:
visible — терминал на экране (пользователь за ПК, видит ход работы и может вмешаться),
headless — `claude -p` в фоне, результат уезжает событием на шину (Core доставит
голосом/push). Режим auto выбирается по простою ввода: пользователь активен → visible.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
from typing import Any

from friday.agents.desktop import skills, winctl

# События завершения фоновых задач: (тип события, данные). Публикует на шину общий
# рантайм агента (shared/agent.py) — навык сам доступа к шине не имеет.
EVENTS: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

TASK_DONE_EVENT = "claude_task_done"

_MODES = ("auto", "visible", "headless")
_DEFAULT_TASK_TIMEOUT = 900.0
_DEFAULT_VISIBLE_IDLE = 300.0
_RESULT_LIMIT = 4000

# Терминалы Linux по убыванию предпочтения: (бинарь, флаги перед командой)
_LINUX_TERMINALS = (
    ("gnome-terminal", ("--",)),
    ("konsole", ("-e",)),
    ("xfce4-terminal", ("-x",)),
    ("x-terminal-emulator", ("-e",)),
    ("xterm", ("-e",)),
)

# Фоновые headless-задачи: держим ссылки, чтобы их не собрал GC до завершения.
_bg_tasks: set[asyncio.Task[None]] = set()


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


async def _capture(*argv: str) -> str:
    """Выполнить утилиту и вернуть stdout; RuntimeError при ненулевом коде выхода."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[:200])
    return stdout.decode(errors="replace").strip()


async def user_idle_seconds() -> float | None:
    """Секунды простоя ввода пользователя; None — определить не удалось."""
    if sys.platform == "win32":
        try:
            return winctl.idle_seconds()
        except Exception:  # noqa: BLE001 — presence best-effort, не роняем навык
            return None
    # Wayland+GNOME: Mutter IdleMonitor через busctl (ответ вида "t 123456", мс)
    busctl = shutil.which("busctl")
    if busctl is not None:
        try:
            out = await _capture(
                busctl,
                "--user",
                "call",
                "org.gnome.Mutter.IdleMonitor",
                "/org/gnome/Mutter/IdleMonitor/Core",
                "org.gnome.Mutter.IdleMonitor",
                "GetIdletime",
            )
            return float(out.split()[1]) / 1000.0
        except Exception:  # noqa: BLE001 — пробуем следующий способ
            pass
    # X11: xprintidle (мс)
    xprintidle = shutil.which("xprintidle")
    if xprintidle is not None:
        try:
            return float(await _capture(xprintidle)) / 1000.0
        except Exception:  # noqa: BLE001 — presence best-effort
            pass
    return None


async def _resolve_mode(mode: str) -> str:
    if mode != "auto":
        return mode
    idle = await user_idle_seconds()
    threshold = _float_env("FRIDAY_CLAUDE_VISIBLE_IDLE", _DEFAULT_VISIBLE_IDLE)
    if idle is not None and idle < threshold:
        return "visible"
    return "headless"


async def _spawn_visible(claude: str, task: str, cwd: str | None) -> str:
    """Открыть терминал с интерактивной сессией Claude Code. Возвращает имя терминала."""
    if sys.platform == "win32":
        # start — встроенная команда cmd; первый аргумент в кавычках — заголовок окна
        await skills.spawn_detached(
            "cmd", "/c", "start", "Пятница — Claude Code", "cmd", "/k", claude, task, cwd=cwd
        )
        return "cmd"
    for name, flags in _LINUX_TERMINALS:
        term = shutil.which(name)
        if term is not None:
            await skills.spawn_detached(term, *flags, claude, task, cwd=cwd)
            return name
    raise RuntimeError(
        "не найден терминал для visible-режима "
        f"({', '.join(name for name, _ in _LINUX_TERMINALS)})"
    )


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Убить процесс со всеми потомками: Claude Code плодит дочерние процессы,
    и выжившие внуки держали бы pipe'ы открытыми (proc.wait() бы завис)."""
    if sys.platform == "win32":
        # голый proc.kill() убил бы только обёртку (cmd/лаунчер), а node-потомки
        # продолжили бы выполнять задачу; taskkill /T валит всё дерево
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


async def _execute_headless(
    claude: str, task: str, cwd: str | None, timeout: float
) -> tuple[bool, str]:
    """Выполнить `claude -p` и вернуть (ok, текст результата/ошибки)."""
    kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True  # своя process group — для _kill_tree
    proc = await asyncio.create_subprocess_exec(
        claude,
        "-p",
        task,
        "--output-format",
        "json",
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        _kill_tree(proc)
        await proc.wait()  # группа убита, pipe'ы закрыты — зомби не остаётся
        return False, f"задача превысила таймаут {timeout:.0f}с и была остановлена"
    out = stdout.decode(errors="replace").strip()
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or out
        return False, f"claude завершился с кодом {proc.returncode}: {detail[:500]}"
    try:
        payload = json.loads(out)
        text = str(payload.get("result") or "").strip() or out
        return not bool(payload.get("is_error")), text
    except ValueError:
        # не JSON — отдаём как есть (лучше сырой текст, чем потерянный результат)
        return True, out


async def _run_headless(claude: str, task: str, cwd: str | None) -> None:
    timeout = _float_env("FRIDAY_CLAUDE_TASK_TIMEOUT", _DEFAULT_TASK_TIMEOUT)
    try:
        ok, text = await _execute_headless(claude, task, cwd, timeout)
    except Exception as exc:  # noqa: BLE001 — итог с ошибкой лучше молчания
        ok, text = False, f"сбой запуска claude: {exc}"
    await EVENTS.put(
        (TASK_DONE_EVENT, {"task": task[:200], "ok": ok, "result": text[:_RESULT_LIMIT]})
    )


async def run_claude_task(params: dict[str, Any]) -> dict[str, Any]:
    task = str(params.get("task", "")).strip()
    if not task:
        raise ValueError("нужен параметр task — самодостаточное ТЗ для Claude Code")
    mode = str(params.get("mode", "auto")).strip().lower() or "auto"
    if mode not in _MODES:
        raise ValueError(f"mode должен быть одним из: {', '.join(_MODES)}")
    raw_cwd = str(params.get("cwd", "")).strip()
    cwd: str | None = None
    if raw_cwd:
        expanded = os.path.expanduser(raw_cwd)
        if not os.path.isdir(expanded):
            raise ValueError(f"cwd '{raw_cwd}' не существует или не папка")
        cwd = expanded

    claude = shutil.which("claude")
    if claude is None:
        raise RuntimeError("Claude Code не установлен на этой машине ('claude' нет в PATH)")

    resolved = await _resolve_mode(mode)
    if resolved == "visible":
        terminal = await _spawn_visible(claude, task, cwd)
        return {
            "mode": "visible",
            "started": True,
            "terminal": terminal,
            "note": "сессия Claude Code открыта в терминале на экране пользователя",
        }

    bg = asyncio.create_task(_run_headless(claude, task, cwd))
    _bg_tasks.add(bg)
    bg.add_done_callback(_bg_tasks.discard)
    return {
        "mode": "headless",
        "started": True,
        "note": (
            "задача выполняется в фоне, займёт минуты; результат придёт отдельным "
            "сообщением — НЕ жди его в этом диалоге и НЕ считай задачу выполненной"
        ),
    }
