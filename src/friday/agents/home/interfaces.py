"""Интерфейс контроллера умного дома (§2.4 мастер-плана).

Protocol, а не база: реализации (mock, Home Assistant) подставляются фабрикой по конфигу,
код агента от конкретного хаба не зависит. Новое устройство добавляется в хаб (HA),
а не в код.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class HomeEntity:
    """Сущность умного дома: лампа, розетка, сцена, датчик…"""

    id: str  # например light.bedroom (домен до точки)
    name: str  # человеческое имя («свет в спальне»)
    state: str  # on | off | значение датчика | scening
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return self.id.split(".", 1)[0]


class DeviceController(Protocol):
    """Контроллер устройств умного дома. Все методы — I/O-bound (сеть до хаба)."""

    async def list_entities(self) -> list[HomeEntity]:
        """Все известные сущности (устройства и сцены)."""
        ...

    async def find(self, query: str) -> HomeEntity | None:
        """Найти сущность по id или подстроке человеческого имени (без регистра)."""
        ...

    async def get_state(self, entity_id: str) -> HomeEntity:
        """Текущее состояние сущности. KeyError, если сущности нет."""
        ...

    async def set_state(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> HomeEntity:
        """Перевести сущность в состояние on|off (+ атрибуты: яркость и т.п.)."""
        ...

    async def run_scene(self, scene_id: str) -> None:
        """Запустить сцену (для HA — scene.turn_on)."""
        ...
