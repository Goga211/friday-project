"""Сабпроцессы для приложений Пятницы: sync subprocess в пуле потоков.

Приложения работают на селекторном event loop (см. shared/aio.py — этого
требует aiomqtt), а SelectorEventLoop на Windows не реализует
asyncio-сабпроцессы. Поэтому процессы запускаются блокирующим subprocess в
отдельном потоке — одинаково на всех ОС и на любом event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from typing import Any


async def run(
    *argv: str,
    stdin_data: bytes | None = None,
    timeout: float | None = None,
    cwd: str | None = None,
    kill_tree: bool = False,
) -> tuple[int, bytes, bytes]:
    """Выполнить процесс и вернуть (код выхода, stdout, stderr).

    При превышении timeout процесс убивается и поднимается TimeoutError.
    kill_tree=True — убивать со всеми потомками (иначе выжившие внуки держали бы
    pipe'ы открытыми и дочитывание после kill зависло бы).
    """
    return await asyncio.to_thread(_run_sync, argv, stdin_data, timeout, cwd, kill_tree)


async def spawn_detached(*argv: str, cwd: str | None = None) -> None:
    """Запустить и забыть (GUI-приложение/браузер), не блокируя агента."""
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def _run_sync(
    argv: tuple[str, ...],
    stdin_data: bytes | None,
    timeout: float | None,
    cwd: str | None,
    kill_tree: bool,
) -> tuple[int, bytes, bytes]:
    kwargs: dict[str, Any] = {}
    if kill_tree and sys.platform != "win32":
        kwargs["start_new_session"] = True  # своя process group — чтобы убить всех потомков
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        **kwargs,
    )
    try:
        stdout, stderr = process.communicate(stdin_data, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill(process, kill_tree)
        process.communicate()  # дочитать pipe'ы и дождаться выхода — не оставить зомби
        raise TimeoutError(f"процесс не уложился в {exc.timeout:.0f} с: {argv[0]}") from exc
    return process.wait(), stdout, stderr


def _kill(process: subprocess.Popen[bytes], kill_tree: bool) -> None:
    if not kill_tree:
        process.kill()
        return
    if sys.platform == "win32":
        # голый kill() убил бы только обёртку (cmd/лаунчер), а потомки продолжили бы
        # работать и держать pipe'ы; taskkill /T валит всё дерево
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
