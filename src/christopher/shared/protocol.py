"""Контракт сообщений на шине. Иммутабельные DTO на Pydantic v2.

Сериализуются в JSON и ходят по MQTT. Любая новая команда/событие/возможность
добавляется здесь, чтобы Core и агенты говорили на одном языке.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid4())


class RiskLevel(StrEnum):
    """Уровень риска действия — определяет, нужно ли подтверждение (см. §4 плана)."""

    safe = "safe"  # чтение/информация — выполняется сразу
    confirm = "confirm"  # запуск/закрытие/запись — требует подтверждения
    dangerous = "dangerous"  # shutdown/удаление/shell/покупки — явное подтверждение


class Capability(BaseModel):
    """Одна возможность устройства, объявляемая в манифесте."""

    name: str
    description: str
    risk: RiskLevel = RiskLevel.safe
    params_schema: dict[str, Any] = Field(default_factory=dict)


class CapabilityManifest(BaseModel):
    """Манифест возможностей устройства (публикуется retained в registry-топик)."""

    device_id: str
    platform: str
    online: bool = True
    ts: datetime = Field(default_factory=_now)
    capabilities: list[Capability] = Field(default_factory=list)


class Command(BaseModel):
    """Команда от Core к агенту."""

    id: str = Field(default_factory=_uuid)
    ts: datetime = Field(default_factory=_now)
    source: str  # кто отправил (обычно christopher-core)
    target: str  # device_id получателя
    action: str  # имя возможности
    params: dict[str, Any] = Field(default_factory=dict)
    requires_confirm: bool = False  # явное подтверждение для confirm/dangerous


class Response(BaseModel):
    """Ответ агента на команду."""

    id: str = Field(default_factory=_uuid)
    correlation_id: str  # id исходной команды
    ts: datetime = Field(default_factory=_now)
    source: str  # device_id агента
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class Event(BaseModel):
    """Асинхронное событие/телеметрия от агента."""

    id: str = Field(default_factory=_uuid)
    ts: datetime = Field(default_factory=_now)
    source: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class UserMessage(BaseModel):
    """Запрос пользователя к ассистенту (из CLI/HUD/голоса)."""

    id: str = Field(default_factory=_uuid)
    ts: datetime = Field(default_factory=_now)
    text: str


class AssistantReply(BaseModel):
    """Ответ ассистента пользователю."""

    id: str = Field(default_factory=_uuid)
    correlation_id: str  # id исходного UserMessage
    ts: datetime = Field(default_factory=_now)
    text: str
