"""Агент умного дома: возможности home_* поверх общего рантайма агента.

Живёт на Hub'е рядом с Core. Контроллер (mock | Home Assistant) — за DeviceController;
возможности агента автоматически становятся инструментами мозга через манифест.
Все действия — RiskLevel.safe (решение пользователя: свет/розетки без подтверждений;
ужесточение при появлении замков/климата — правкой реестра, не архитектуры).
"""

from __future__ import annotations

import contextlib
import platform
from typing import Any

from friday.agents.home.config import HomeSettings
from friday.agents.home.factory import build_controller
from friday.agents.home.interfaces import DeviceController, HomeEntity
from friday.shared import aio
from friday.shared.agent import CapabilityRegistry, run_capability_agent
from friday.shared.config import BusSettings
from friday.shared.logging import setup_logging
from friday.shared.protocol import Capability, RiskLevel

_STRING = {"type": "string"}


def _entity_payload(entity: HomeEntity) -> dict[str, Any]:
    return {
        "id": entity.id,
        "name": entity.name,
        "domain": entity.domain,
        "state": entity.state,
        "attributes": entity.attributes,
    }


async def _resolve(controller: DeviceController, params: dict[str, Any]) -> HomeEntity:
    query = str(params.get("entity", "")).strip()
    if not query:
        raise ValueError("нужен параметр entity (id или название, см. home_list)")
    entity = await controller.find(query)
    if entity is None:
        raise ValueError(f"не нашёл сущность '{query}' (список — home_list)")
    return entity


def build_registry(controller: DeviceController) -> CapabilityRegistry:
    """Реестр возможностей агента поверх конкретного контроллера."""

    async def home_list(params: dict[str, Any]) -> dict[str, Any]:
        entities = await controller.list_entities()
        return {"entities": [_entity_payload(e) for e in entities]}

    async def home_get_state(params: dict[str, Any]) -> dict[str, Any]:
        entity = await _resolve(controller, params)
        return _entity_payload(await controller.get_state(entity.id))

    async def home_set_state(params: dict[str, Any]) -> dict[str, Any]:
        entity = await _resolve(controller, params)
        state = str(params.get("state", "")).strip().lower()
        if state not in ("on", "off"):
            raise ValueError("state должен быть on или off")
        attributes = params.get("attributes")
        if attributes is not None and not isinstance(attributes, dict):
            raise ValueError("attributes должен быть объектом")
        updated = await controller.set_state(entity.id, state, attributes)
        return _entity_payload(updated)

    async def home_run_scene(params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "entity": params.get("scene") or params.get("entity")}
        entity = await _resolve(controller, params)
        await controller.run_scene(entity.id)
        return {"scene": entity.id, "activated": True}

    return {
        "home_list": (
            Capability(
                name="home_list",
                description=(
                    "Умный дом: список всех устройств и сцен (id, название, домен, состояние)"
                ),
                risk=RiskLevel.safe,
            ),
            home_list,
        ),
        "home_get_state": (
            Capability(
                name="home_get_state",
                description=("Умный дом: состояние устройства (params: entity — id или название)"),
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {"entity": _STRING},
                    "required": ["entity"],
                },
            ),
            home_get_state,
        ),
        "home_set_state": (
            Capability(
                name="home_set_state",
                description=(
                    "Умный дом: включить/выключить устройство (params: entity — id или "
                    "название, state: on|off, attributes — опц., например {brightness: 40})"
                ),
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {
                        "entity": _STRING,
                        "state": {"type": "string", "enum": ["on", "off"]},
                        "attributes": {"type": "object"},
                    },
                    "required": ["entity", "state"],
                },
            ),
            home_set_state,
        ),
        "home_run_scene": (
            Capability(
                name="home_run_scene",
                description=("Умный дом: запустить сцену (params: scene — id или название сцены)"),
                risk=RiskLevel.safe,
                params_schema={
                    "type": "object",
                    "properties": {"scene": _STRING},
                    "required": ["scene"],
                },
            ),
            home_run_scene,
        ),
    }


def _default_device_id() -> str:
    return f"home-{platform.node() or 'hub'}"


async def run() -> None:
    setup_logging()
    bus_settings = BusSettings()
    home_settings = HomeSettings()
    controller = build_controller(home_settings)
    device_id = bus_settings.device_id or _default_device_id()
    registry = build_registry(controller)
    await run_capability_agent(bus_settings, device_id, "home", registry)


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        aio.run(run())


if __name__ == "__main__":
    main()
