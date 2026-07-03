"""HomeAssistantController: адаптер к REST API Home Assistant (ADR 0003).

Устройства добавляются в HA, а не в код: адаптер написан один раз и работает с любой
сущностью через /api/states и /api/services. Аутентификация — long-lived access token.
Живой прогон требует работающего HA; юнит-тесты гоняют адаптер на httpx.MockTransport.
"""

from __future__ import annotations

from typing import Any

import httpx

from friday.agents.home.config import HomeSettings
from friday.agents.home.interfaces import HomeEntity


def _to_entity(payload: dict[str, Any]) -> HomeEntity:
    attributes = dict(payload.get("attributes") or {})
    name = str(attributes.pop("friendly_name", "") or payload["entity_id"])
    return HomeEntity(
        id=str(payload["entity_id"]),
        name=name,
        state=str(payload.get("state", "unknown")),
        attributes=attributes,
    )


class HomeAssistantController:
    def __init__(self, settings: HomeSettings) -> None:
        if not settings.ha_token:
            raise RuntimeError("нужен FRIDAY_HOME_HA_TOKEN (long-lived token из профиля HA)")
        self._client = httpx.AsyncClient(
            base_url=settings.ha_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.ha_token}"},
            timeout=settings.ha_timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def list_entities(self) -> list[HomeEntity]:
        response = await self._client.get("/api/states")
        response.raise_for_status()
        return [_to_entity(item) for item in response.json()]

    async def find(self, query: str) -> HomeEntity | None:
        wanted = query.strip().casefold()
        for entity in await self.list_entities():
            if entity.id == query or wanted in entity.name.casefold():
                return entity
        return None

    async def get_state(self, entity_id: str) -> HomeEntity:
        response = await self._client.get(f"/api/states/{entity_id}")
        if response.status_code == 404:
            raise KeyError(f"нет сущности '{entity_id}'")
        response.raise_for_status()
        return _to_entity(response.json())

    async def set_state(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> HomeEntity:
        if state not in ("on", "off"):
            raise ValueError("state должен быть on или off")
        domain = entity_id.split(".", 1)[0]
        service = "turn_on" if state == "on" else "turn_off"
        payload: dict[str, Any] = {"entity_id": entity_id}
        if state == "on" and attributes:
            payload.update(attributes)  # яркость/цвет и т.п. — параметры turn_on
        response = await self._client.post(f"/api/services/{domain}/{service}", json=payload)
        response.raise_for_status()
        return await self.get_state(entity_id)

    async def run_scene(self, scene_id: str) -> None:
        response = await self._client.post(
            "/api/services/scene/turn_on", json={"entity_id": scene_id}
        )
        response.raise_for_status()
