"""Общий рантайм агента-исполнителя на шине.

Один жизненный цикл для любого агента с реестром возможностей (desktop, home, …):
манифест retained + Last-Will на offline → подписка на свой cmd-топик → диспатч команд
с проверкой уровня риска → ответ в resp-топик. При штатном выходе публикуется
offline-манифест (Will срабатывает только при обрыве).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiomqtt

from friday.shared.bus import Bus, run_with_reconnect
from friday.shared.config import BusSettings
from friday.shared.net import detect_mac
from friday.shared.protocol import (
    Capability,
    CapabilityManifest,
    Command,
    Event,
    Response,
    RiskLevel,
)
from friday.shared.topics import cmd_topic, event_topic, registry_topic, resp_topic

log = logging.getLogger("friday.agent")

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
# имя возможности → (описание, обработчик)
CapabilityRegistry = dict[str, tuple[Capability, Handler]]
# очередь исходящих событий агента: (тип события, данные)
EventQueue = asyncio.Queue[tuple[str, dict[str, Any]]]


async def dispatch(cmd: Command, registry: CapabilityRegistry) -> Response:
    """Выполнить команду по реестру возможностей с проверкой уровня риска."""
    source = cmd.target
    entry = registry.get(cmd.action)
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


def _build_manifest(
    settings: BusSettings,
    device_id: str,
    platform_name: str,
    registry: CapabilityRegistry,
    online: bool,
) -> CapabilityManifest:
    return CapabilityManifest(
        device_id=device_id,
        platform=platform_name,
        online=online,
        capabilities=[cap for cap, _ in registry.values()],
        alias=settings.device_alias,
        mac=settings.device_mac or detect_mac(),
    )


async def publish_events(bus: Bus, device_id: str, events: EventQueue) -> None:
    """Публиковать события агента из очереди на шину (крутится до отмены)."""
    while True:
        event_type, data = await events.get()
        await bus.publish_model(
            event_topic(device_id, event_type),
            Event(source=device_id, type=event_type, data=data),
        )


async def run_capability_agent(
    settings: BusSettings,
    device_id: str,
    platform_name: str,
    registry: CapabilityRegistry,
    events: EventQueue | None = None,
) -> None:
    """Крутить агента с авто-переподключением к брокеру (блокирует до отмены)."""
    log.info(
        "Агент '%s' стартует, брокер %s:%s",
        device_id,
        settings.broker_host,
        settings.broker_port,
    )
    await run_with_reconnect(
        functools.partial(_session, settings, device_id, platform_name, registry, events),
        initial_delay=settings.reconnect_initial_delay,
        max_delay=settings.reconnect_max_delay,
    )


async def _session(
    settings: BusSettings,
    device_id: str,
    platform_name: str,
    registry: CapabilityRegistry,
    events: EventQueue | None = None,
) -> None:
    """Один жизненный цикл соединения: манифест → подписка → цикл команд."""
    offline = _build_manifest(settings, device_id, platform_name, registry, online=False)
    will = aiomqtt.Will(
        topic=registry_topic(device_id),
        payload=offline.model_dump_json().encode(),
        qos=1,
        retain=True,
    )

    async with Bus(settings, client_id=device_id, will=will) as bus:
        online = _build_manifest(settings, device_id, platform_name, registry, online=True)
        await bus.publish_model(registry_topic(device_id), online, retain=True)
        await bus.subscribe(cmd_topic(device_id))
        log.info(
            "Агент '%s' подключён, объявил %d возможностей, слушаю команды",
            device_id,
            len(online.capabilities),
        )

        publisher: asyncio.Task[None] | None = None
        if events is not None:
            publisher = asyncio.create_task(publish_events(bus, device_id, events))
        try:
            async for msg in bus.messages:
                payload = msg.payload
                if not isinstance(payload, (bytes, bytearray)):
                    continue
                cmd = Command.model_validate_json(bytes(payload))
                log.info("← cmd %s (id=%s) от %s", cmd.action, cmd.id[:8], cmd.source)
                resp = await dispatch(cmd, registry)
                await bus.publish_model(resp_topic(cmd.id), resp)
        finally:
            if publisher is not None:
                publisher.cancel()
            # штатный выход: явно публикуем offline (Will срабатывает только при обрыве)
            with contextlib.suppress(Exception):
                await bus.publish_model(
                    registry_topic(device_id),
                    _build_manifest(settings, device_id, platform_name, registry, online=False),
                    retain=True,
                )
