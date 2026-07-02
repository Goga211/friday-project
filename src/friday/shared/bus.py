"""Тонкая async-обёртка над MQTT-клиентом (aiomqtt).

Не прячет aiomqtt целиком — только даёт удобный publish/subscribe для Pydantic-моделей,
сборку клиента из настроек (включая TLS) и поток входящих сообщений.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType
from typing import Self

import aiomqtt
from pydantic import BaseModel

from friday.shared.config import BusSettings


class Bus:
    def __init__(
        self,
        settings: BusSettings,
        client_id: str,
        will: aiomqtt.Will | None = None,
    ) -> None:
        self._settings = settings
        self._client_id = client_id
        self._will = will
        self._client: aiomqtt.Client | None = None

    def _build_client(self) -> aiomqtt.Client:
        tls_params: aiomqtt.TLSParameters | None = None
        if self._settings.tls:
            tls_params = aiomqtt.TLSParameters(
                ca_certs=self._settings.tls_ca,
                certfile=self._settings.tls_cert,
                keyfile=self._settings.tls_key,
            )
        return aiomqtt.Client(
            hostname=self._settings.broker_host,
            port=self._settings.broker_port,
            identifier=self._client_id,
            tls_params=tls_params,
            will=self._will,
        )

    async def __aenter__(self) -> Self:
        self._client = self._build_client()
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, tb)

    @property
    def _required_client(self) -> aiomqtt.Client:
        if self._client is None:
            raise RuntimeError("Bus используется вне async-контекста (нет соединения)")
        return self._client

    async def subscribe(self, topic: str, qos: int = 1) -> None:
        await self._required_client.subscribe(topic, qos=qos)

    async def publish_model(
        self,
        topic: str,
        model: BaseModel,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        await self._required_client.publish(
            topic,
            payload=model.model_dump_json().encode(),
            qos=qos,
            retain=retain,
        )

    @property
    def messages(self) -> AsyncIterator[aiomqtt.Message]:
        return self._required_client.messages
