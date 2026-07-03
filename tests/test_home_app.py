"""Возможности агента умного дома (home_*) на MockController — через общий dispatch."""

from __future__ import annotations

import pytest

from friday.agents.home.app import build_registry
from friday.agents.home.config import HomeSettings
from friday.agents.home.factory import build_controller
from friday.agents.home.mock import MockController
from friday.shared.agent import CapabilityRegistry, dispatch
from friday.shared.protocol import Command


@pytest.fixture()
def registry() -> CapabilityRegistry:
    return build_registry(MockController())


async def _run(registry: CapabilityRegistry, action: str, params: dict) -> dict:
    cmd = Command(source="core", target="home-test", action=action, params=params)
    resp = await dispatch(cmd, registry)
    assert resp.ok, resp.error
    assert resp.result is not None
    return resp.result


@pytest.mark.asyncio
async def test_home_list(registry: CapabilityRegistry) -> None:
    result = await _run(registry, "home_list", {})
    ids = [e["id"] for e in result["entities"]]
    assert "light.bedroom" in ids and "scene.evening" in ids


@pytest.mark.asyncio
async def test_set_state_by_human_name_then_get(registry: CapabilityRegistry) -> None:
    updated = await _run(
        registry,
        "home_set_state",
        {"entity": "свет в спальне", "state": "on", "attributes": {"brightness": 40}},
    )
    assert updated["state"] == "on"

    current = await _run(registry, "home_get_state", {"entity": "свет в спальне"})
    assert current["state"] == "on"
    assert current["attributes"]["brightness"] == 40


@pytest.mark.asyncio
async def test_unknown_entity_friendly_error(registry: CapabilityRegistry) -> None:
    cmd = Command(
        source="core",
        target="h",
        action="home_set_state",
        params={"entity": "тостер", "state": "on"},
    )
    resp = await dispatch(cmd, registry)
    assert resp.ok is False
    assert "home_list" in (resp.error or "")  # подсказка мозгу, где взять список


@pytest.mark.asyncio
async def test_set_state_validates_state(registry: CapabilityRegistry) -> None:
    cmd = Command(
        source="core",
        target="h",
        action="home_set_state",
        params={"entity": "light.bedroom", "state": "disco"},
    )
    resp = await dispatch(cmd, registry)
    assert resp.ok is False


@pytest.mark.asyncio
async def test_run_scene_by_name(registry: CapabilityRegistry) -> None:
    result = await _run(registry, "home_run_scene", {"scene": "вечер"})
    assert result == {"scene": "scene.evening", "activated": True}
    bedroom = await _run(registry, "home_get_state", {"entity": "light.bedroom"})
    assert bedroom["state"] == "on"


@pytest.mark.asyncio
async def test_all_capabilities_are_safe(registry: CapabilityRegistry) -> None:
    from friday.shared.protocol import RiskLevel

    assert all(cap.risk is RiskLevel.safe for cap, _ in registry.values())


def test_factory_builds_mock_and_rejects_unknown() -> None:
    assert isinstance(build_controller(HomeSettings(controller="mock")), MockController)
    with pytest.raises(ValueError):
        build_controller(HomeSettings(controller="zigbee"))
