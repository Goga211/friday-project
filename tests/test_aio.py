"""Тесты friday.shared.aio — запуск приложений на селекторном event loop.

Регресс виндового переезда: дефолтный ProactorEventLoop не умеет
add_reader/add_writer, из-за чего aiomqtt падал с NotImplementedError.
"""

from __future__ import annotations

import asyncio
import sys

from friday.shared import aio, proc


def test_run_executes_coroutine() -> None:
    done: list[bool] = []

    async def work() -> None:
        done.append(True)

    aio.run(work())
    assert done == [True]


def test_run_uses_selector_event_loop() -> None:
    loops: list[asyncio.AbstractEventLoop] = []

    async def work() -> None:
        loops.append(asyncio.get_running_loop())

    aio.run(work())
    assert isinstance(loops[0], asyncio.SelectorEventLoop)


def test_run_supports_subprocesses_via_proc() -> None:
    # На виндовом SelectorEventLoop asyncio-сабпроцессы не работают —
    # поэтому агенты обязаны запускать процессы через friday.shared.proc
    results: list[tuple[int, bytes, bytes]] = []

    async def work() -> None:
        results.append(await proc.run(sys.executable, "-c", "print('ok')"))

    aio.run(work())
    code, stdout, _ = results[0]
    assert code == 0
    assert stdout.strip() == b"ok"
