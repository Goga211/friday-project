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


def user_reply_topic(correlation_id: str) -> str:
    """Ответ ассистента пользователю (по id UserMessage)."""
    return f"{PREFIX}/user/reply/{correlation_id}"


USER_REQUEST = f"{PREFIX}/user/request"
USER_CONFIRM = f"{PREFIX}/user/confirm"
USER_REPLY_WILDCARD = f"{PREFIX}/user/reply/+"
REGISTRY_WILDCARD = f"{PREFIX}/registry/+"
RESP_WILDCARD = f"{PREFIX}/resp/+"
EVENT_WILDCARD = f"{PREFIX}/event/#"

# Голосовой канал (для HUD/наблюдаемости): что услышали и что произносим.
VOICE_TRANSCRIPT = f"{PREFIX}/voice/transcript"
VOICE_SAY = f"{PREFIX}/voice/say"


def device_from_registry_topic(topic: str) -> str:
    """Извлечь device_id из registry-топика."""
    return topic.rsplit("/", 1)[-1]
