"""MockController: поиск, переходы состояний, сцена."""

from __future__ import annotations

import pytest

from friday.agents.home.mock import MockController


@pytest.mark.asyncio
async def test_find_by_id_and_name_substring() -> None:
    home = MockController()
    by_id = await home.find("light.bedroom")
    assert by_id is not None and by_id.id == "light.bedroom"
    by_name = await home.find("Свет в СПАЛЬНЕ")
    assert by_name is not None and by_name.id == "light.bedroom"
    assert await home.find("тостер") is None


@pytest.mark.asyncio
async def test_set_state_transitions_and_merges_attributes() -> None:
    home = MockController()
    updated = await home.set_state("light.bedroom", "on", {"brightness": 40})
    assert updated.state == "on"
    assert updated.attributes["brightness"] == 40
    # состояние честно сохраняется
    current = await home.get_state("light.bedroom")
    assert current.state == "on"


@pytest.mark.asyncio
async def test_get_state_unknown_entity_raises() -> None:
    home = MockController()
    with pytest.raises(KeyError):
        await home.get_state("light.nope")


@pytest.mark.asyncio
async def test_run_scene_changes_lights() -> None:
    home = MockController()
    await home.run_scene("scene.evening")
    bedroom = await home.get_state("light.bedroom")
    assert bedroom.state == "on"
    assert bedroom.attributes["brightness"] == 40


@pytest.mark.asyncio
async def test_run_unknown_scene_raises() -> None:
    home = MockController()
    with pytest.raises(KeyError):
        await home.run_scene("scene.nope")


@pytest.mark.asyncio
async def test_entity_domain_property() -> None:
    home = MockController()
    entity = await home.get_state("switch.kettle")
    assert entity.domain == "switch"
