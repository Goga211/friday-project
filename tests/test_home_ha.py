"""HomeAssistantController против фейкового HA (httpx.MockTransport, без сети)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from friday.agents.home.config import HomeSettings
from friday.agents.home.ha import HomeAssistantController

_STATES: list[dict[str, Any]] = [
    {
        "entity_id": "light.bedroom",
        "state": "off",
        "attributes": {"friendly_name": "Свет в спальне", "supported_features": 44},
    },
    {"entity_id": "scene.evening", "state": "idle", "attributes": {"friendly_name": "Вечер"}},
]


def _fake_ha(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.headers.get("Authorization") != "Bearer test-token":
            return httpx.Response(401)
        path = request.url.path
        if request.method == "GET" and path == "/api/states":
            return httpx.Response(200, json=_STATES)
        if request.method == "GET" and path.startswith("/api/states/"):
            entity_id = path.removeprefix("/api/states/")
            for state in _STATES:
                if state["entity_id"] == entity_id:
                    return httpx.Response(200, json=state)
            return httpx.Response(404)
        if request.method == "POST" and path.startswith("/api/services/"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _controller(calls: list[httpx.Request]) -> HomeAssistantController:
    settings = HomeSettings(controller="ha", ha_url="http://ha.local:8123", ha_token="test-token")
    controller = HomeAssistantController(settings)
    # подменяем транспорт клиента на фейковый HA (base_url/заголовки сохраняются)
    controller._client = httpx.AsyncClient(
        base_url="http://ha.local:8123",
        headers={"Authorization": "Bearer test-token"},
        transport=_fake_ha(calls),
    )
    return controller


def test_requires_token() -> None:
    with pytest.raises(RuntimeError, match="HA_TOKEN"):
        HomeAssistantController(HomeSettings(controller="ha", ha_token=None))


@pytest.mark.asyncio
async def test_list_and_find_map_friendly_name() -> None:
    controller = _controller([])
    entities = await controller.list_entities()
    assert [e.id for e in entities] == ["light.bedroom", "scene.evening"]
    assert entities[0].name == "Свет в спальне"
    assert entities[0].attributes["supported_features"] == 44  # friendly_name изъят в name

    found = await controller.find("свет в спальне")
    assert found is not None and found.id == "light.bedroom"
    await controller.close()


@pytest.mark.asyncio
async def test_set_state_calls_domain_service() -> None:
    calls: list[httpx.Request] = []
    controller = _controller(calls)
    updated = await controller.set_state("light.bedroom", "on", {"brightness": 40})

    service_calls = [r for r in calls if r.method == "POST"]
    assert len(service_calls) == 1
    request = service_calls[0]
    assert request.url.path == "/api/services/light/turn_on"  # домен из entity_id
    assert json.loads(request.content) == {"entity_id": "light.bedroom", "brightness": 40}
    assert updated.id == "light.bedroom"  # состояние перечитано после вызова
    await controller.close()


@pytest.mark.asyncio
async def test_set_state_off_ignores_attributes() -> None:
    calls: list[httpx.Request] = []
    controller = _controller(calls)
    await controller.set_state("light.bedroom", "off", {"brightness": 40})
    request = next(r for r in calls if r.method == "POST")
    assert request.url.path == "/api/services/light/turn_off"
    assert json.loads(request.content) == {"entity_id": "light.bedroom"}
    await controller.close()


@pytest.mark.asyncio
async def test_set_state_rejects_bad_state() -> None:
    controller = _controller([])
    with pytest.raises(ValueError):
        await controller.set_state("light.bedroom", "disco")
    await controller.close()


@pytest.mark.asyncio
async def test_get_state_unknown_entity_raises_keyerror() -> None:
    controller = _controller([])
    with pytest.raises(KeyError):
        await controller.get_state("light.nope")
    await controller.close()


@pytest.mark.asyncio
async def test_run_scene_calls_scene_turn_on() -> None:
    calls: list[httpx.Request] = []
    controller = _controller(calls)
    await controller.run_scene("scene.evening")
    request = next(r for r in calls if r.method == "POST")
    assert request.url.path == "/api/services/scene/turn_on"
    assert json.loads(request.content) == {"entity_id": "scene.evening"}
    await controller.close()
