"""Desktop-агент (скелет Phase 0).

При старте объявляет манифест возможностей (retained) + ставит Last-Will на offline.
Слушает команды на свой топик, диспатчит на обработчики с проверкой уровня риска,
отвечает в resp-топик. При штатном выходе публикует offline-манифест.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import platform

import aiomqtt

from friday.agents.desktop.capabilities import REGISTRY, manifest_capabilities
from friday.shared.bus import Bus
from friday.shared.config import BusSettings
from friday.shared.logging import setup_logging
from friday.shared.protocol import CapabilityManifest, Command, Response, RiskLevel
from friday.shared.topics import cmd_topic, registry_topic, resp_topic

log = logging.getLogger("friday.desktop")


def _default_device_id() -> str:
    return f"desktop-{platform.node() or 'unknown'}"


def _build_manifest(device_id: str, online: bool) -> CapabilityManifest:
    return CapabilityManifest(
        device_id=device_id,
        platform=platform.system().lower(),
        online=online,
        capabilities=manifest_capabilities(),
    )


async def dispatch(cmd: Command) -> Response:
    source = cmd.target
    entry = REGISTRY.get(cmd.action)
    if entry is None:
        return Response(
            correlation_id=cmd.id,
            source=source,
            ok=False,
            error=f"неизвестное действие: {cmd.action}",
        )

    capability, handler = entry
    # уровни риска: safe — сразу; confirm/dangerous — только при явном подтверждении
    if capability.risk is not RiskLevel.safe and not cmd.requires_confirm:
        return Response(
            correlation_id=cmd.id,
            source=source,
            ok=False,
            error=f"действие '{cmd.action}' уровня {capability.risk.value} требует подтверждения",
        )

    try:
        result = await handler(cmd.params)
        return Response(correlation_id=cmd.id, source=source, ok=True, result=result)
    except Exception as exc:  # noqa: BLE001 — агент не должен падать на ошибке навыка
        log.exception("ошибка при выполнении %s", cmd.action)
        return Response(correlation_id=cmd.id, source=source, ok=False, error=str(exc))


async def run() -> None:
    setup_logging()
    settings = BusSettings()
    device_id = settings.device_id or _default_device_id()

    offline = _build_manifest(device_id, online=False)
    will = aiomqtt.Will(
        topic=registry_topic(device_id),
        payload=offline.model_dump_json().encode(),
        qos=1,
        retain=True,
    )

    log.info(
        "Desktop-агент '%s' стартует, брокер %s:%s",
        device_id,
        settings.broker_host,
        settings.broker_port,
    )

    async with Bus(settings, client_id=device_id, will=will) as bus:
        online = _build_manifest(device_id, online=True)
        await bus.publish_model(registry_topic(device_id), online, retain=True)
        await bus.subscribe(cmd_topic(device_id))
        log.info(
            "Агент подключён, объявил %d возможностей, слушаю команды",
            len(online.capabilities),
        )

        try:
            async for msg in bus.messages:
                payload = msg.payload
                if not isinstance(payload, (bytes, bytearray)):
                    continue
                cmd = Command.model_validate_json(bytes(payload))
                log.info("← cmd %s (id=%s) от %s", cmd.action, cmd.id[:8], cmd.source)
                resp = await dispatch(cmd)
                await bus.publish_model(resp_topic(cmd.id), resp)
        finally:
            # штатный выход: явно публикуем offline (Will срабатывает только при обрыве)
            with contextlib.suppress(Exception):
                await bus.publish_model(
                    registry_topic(device_id),
                    _build_manifest(device_id, online=False),
                    retain=True,
                )


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
