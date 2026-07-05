"""Запуск asyncio-приложений Пятницы: везде селекторный event loop.

aiomqtt работает через loop.add_reader/add_writer, которых нет у виндового
ProactorEventLoop (дефолт на Windows) — MQTT-соединение падает с
NotImplementedError. Селекторный луп на Windows, в свою очередь, не умеет
asyncio-сабпроцессы, поэтому агенты запускают процессы через
friday.shared.proc (sync subprocess в пуле потоков).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any


def run(main: Coroutine[Any, Any, None]) -> None:
    """asyncio.run, но на Windows — на селекторном event loop (ради aiomqtt)."""
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(main)
        return
    asyncio.run(main)
