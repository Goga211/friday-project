"""MockController: умный дом в памяти — пока нет железа (и для тестов).

Честные переходы состояний: «включи свет в спальне» реально меняет состояние, и
последующее «что со светом?» его видит. Сцена «вечер» включает лампы с приглушённой
яркостью — можно прощупать весь путь мозг → агент → контроллер без единого устройства.
"""

from __future__ import annotations

from typing import Any

from friday.agents.home.interfaces import HomeEntity

_DEFAULT_ENTITIES = [
    HomeEntity(id="light.bedroom", name="свет в спальне", state="off"),
    HomeEntity(id="light.livingroom", name="свет в гостиной", state="off"),
    HomeEntity(id="switch.kettle", name="розетка чайника", state="off"),
    HomeEntity(id="scene.evening", name="сцена вечер", state="idle"),
]
# Что делает сцена «вечер» с остальными сущностями
_EVENING_SCENE = {
    "light.bedroom": ("on", {"brightness": 40}),
    "light.livingroom": ("on", {"brightness": 60}),
}


class MockController:
    def __init__(self, entities: list[HomeEntity] | None = None) -> None:
        initial = entities if entities is not None else _DEFAULT_ENTITIES
        self._entities: dict[str, HomeEntity] = {e.id: e for e in initial}

    async def list_entities(self) -> list[HomeEntity]:
        return list(self._entities.values())

    async def find(self, query: str) -> HomeEntity | None:
        if query in self._entities:
            return self._entities[query]
        wanted = query.strip().casefold()
        for entity in self._entities.values():
            if wanted in entity.name.casefold():
                return entity
        return None

    async def get_state(self, entity_id: str) -> HomeEntity:
        entity = self._entities.get(entity_id)
        if entity is None:
            raise KeyError(f"нет сущности '{entity_id}'")
        return entity

    async def set_state(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> HomeEntity:
        current = await self.get_state(entity_id)
        updated = HomeEntity(
            id=current.id,
            name=current.name,
            state=state,
            attributes={**current.attributes, **(attributes or {})},
        )
        self._entities[entity_id] = updated
        return updated

    async def run_scene(self, scene_id: str) -> None:
        await self.get_state(scene_id)  # KeyError, если сцены нет
        for entity_id, (state, attributes) in _EVENING_SCENE.items():
            if entity_id in self._entities:
                await self.set_state(entity_id, state, dict(attributes))
