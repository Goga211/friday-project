"""Desktop-агент: навыки управления компьютером поверх общего рантайма агента.

Реестр возможностей — в capabilities.py (диспатч по ОС — в skills.py); жизненный цикл
(манифест retained + LWT, диспатч с проверкой риска, авто-reconnect) — shared/agent.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import platform

from friday.agents.desktop.capabilities import REGISTRY
from friday.agents.desktop.claude_code import EVENTS
from friday.shared.agent import run_capability_agent
from friday.shared.config import BusSettings
from friday.shared.logging import setup_logging


def _default_device_id() -> str:
    return f"desktop-{platform.node() or 'unknown'}"


async def run() -> None:
    setup_logging()
    settings = BusSettings()
    device_id = settings.device_id or _default_device_id()
    await run_capability_agent(
        settings, device_id, platform.system().lower(), REGISTRY, events=EVENTS
    )


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
