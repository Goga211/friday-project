"""Построение и разбор MQTT-топиков. Единственный источник правды по схеме топиков."""

from __future__ import annotations

PREFIX = "christopher"


def cmd_topic(device_id: str) -> str:
    """Команды от Core агенту."""
    return f"{PREFIX}/cmd/{device_id}"


def resp_topic(correlation_id: str) -> str:
    """Ответ агента (по id команды)."""
    return f"{PREFIX}/resp/{correlation_id}"


def event_topic(device_id: str, event_type: str) -> str:
    """Событие/телеметрия от агента."""
    return f"{PREFIX}/event/{device_id}/{event_type}"


def registry_topic(device_id: str) -> str:
    """Анонс манифеста возможностей (retained)."""
    return f"{PREFIX}/registry/{device_id}"


REGISTRY_WILDCARD = f"{PREFIX}/registry/+"
RESP_WILDCARD = f"{PREFIX}/resp/+"
EVENT_WILDCARD = f"{PREFIX}/event/#"


def device_from_registry_topic(topic: str) -> str:
    """Извлечь device_id из registry-топика."""
    return topic.rsplit("/", 1)[-1]
