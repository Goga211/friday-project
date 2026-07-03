"""Сборка контроллера умного дома по конфигу (mock | ha). Ленивые импорты."""

from __future__ import annotations

from friday.agents.home.config import HomeSettings
from friday.agents.home.interfaces import DeviceController


def build_controller(settings: HomeSettings) -> DeviceController:
    kind = settings.controller.strip().lower()
    if kind == "mock":
        from friday.agents.home.mock import MockController

        return MockController()
    if kind == "ha":
        from friday.agents.home.ha import HomeAssistantController

        return HomeAssistantController(settings)
    raise ValueError(f"неизвестный контроллер умного дома: {kind!r} (mock | ha)")
