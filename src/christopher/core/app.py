"""Core-сервис (скелет Phase 0).

Подключается к шине, слушает анонсы возможностей (registry) и ответы (resp),
ведёт реестр устройств и периодически пингует онлайн-агентов. Мозг (Claude) и
маршрутизация навыков подключаются в Phase 1.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from christopher.core.registry import DeviceRegistry
from christopher.shared.bus import Bus
from christopher.shared.config import BusSettings
from christopher.shared.logging import setup_logging
from christopher.shared.protocol import CapabilityManifest, Command, Response
from christopher.shared.topics import (
    PREFIX,
    REGISTRY_WILDCARD,
    RESP_WILDCARD,
    cmd_topic,
)

log = logging.getLogger("christopher.core")

CORE_ID = "christopher-core"


async def ping_loop(bus: Bus, registry: DeviceRegistry, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        for device_id in registry.online_devices():
            cmd = Command(source=CORE_ID, target=device_id, action="ping")
            await bus.publish_model(cmd_topic(device_id), cmd)
            log.info("→ ping %s (cmd=%s)", device_id, cmd.id[:8])


def _handle_manifest(registry: DeviceRegistry, payload: bytes | bytearray) -> None:
    manifest = CapabilityManifest.model_validate_json(bytes(payload))
    registry.update(manifest)
    status = "online" if manifest.online else "offline"
    caps = ", ".join(c.name for c in manifest.capabilities) or "—"
    log.info(
        "registry: %s [%s] %s | возможности: %s",
        manifest.device_id,
        manifest.platform,
        status,
        caps,
    )


def _handle_response(payload: bytes | bytearray) -> None:
    resp = Response.model_validate_json(bytes(payload))
    if resp.ok:
        log.info("← resp от %s (cmd=%s): %s", resp.source, resp.correlation_id[:8], resp.result)
    else:
        log.warning(
            "← resp от %s (cmd=%s) ОШИБКА: %s",
            resp.source,
            resp.correlation_id[:8],
            resp.error,
        )


async def run() -> None:
    setup_logging()
    settings = BusSettings()
    registry = DeviceRegistry()
    log.info(
        "Core стартует, брокер %s:%s (tls=%s)",
        settings.broker_host,
        settings.broker_port,
        settings.tls,
    )

    async with Bus(settings, client_id=CORE_ID) as bus:
        await bus.subscribe(REGISTRY_WILDCARD)
        await bus.subscribe(RESP_WILDCARD)
        log.info("Core подключён, слушаю registry + responses")

        pinger = asyncio.create_task(ping_loop(bus, registry, settings.ping_interval))
        try:
            async for msg in bus.messages:
                payload = msg.payload
                if not isinstance(payload, (bytes, bytearray)):
                    continue
                topic = str(msg.topic)
                if topic.startswith(f"{PREFIX}/registry/"):
                    _handle_manifest(registry, payload)
                elif topic.startswith(f"{PREFIX}/resp/"):
                    _handle_response(payload)
        finally:
            pinger.cancel()


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
