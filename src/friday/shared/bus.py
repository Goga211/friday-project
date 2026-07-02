"""Тонкая async-обёртка над MQTT-клиентом (aiomqtt).

Не прячет aiomqtt целиком — только даёт удобный publish/subscribe для Pydantic-моделей,
сборку клиента из настроек (включая TLS) и поток входящих сообщений. Плюс
run_with_reconnect — цикл жизни агента с авто-переподключением при разрывах.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from types import TracebackType
from typing import Self

import aiomqtt
from pydantic import BaseModel

from friday.shared.config import BusSettings

log = logging.getLogger("friday.bus")

# Сколько секунд сессия должна прожить, чтобы считать связь стабильной и сбросить backoff.
STABLE_SESSION_SECONDS = 60.0


async def run_with_reconnect(
    session: Callable[[], Awaitable[None]],
    *,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Крутит session() с авто-переподключением при разрывах MQTT.

    session — один жизненный цикл соединения (connect → subscribe → цикл сообщений).
    Штатный return из session завершает и этот цикл. При MqttError (в том числе внутри
    ExceptionGroup, если сессия построена на TaskGroup) ждём с экспоненциальным backoff
    и заходим снова; после стабильной сессии задержка сбрасывается. Прочие ошибки —
    наружу: это баги, а не сетевые сбои.
    """
    delay = initial_delay
    while True:
        started = time.monotonic()
        try:
            await session()
            return
        except aiomqtt.MqttError as exc:
            error: BaseException = exc
        except BaseExceptionGroup as eg:
            mqtt_only, rest = eg.split(aiomqtt.MqttError)
            if rest is not None or mqtt_only is None:
                raise
            error = mqtt_only
        if time.monotonic() - started >= STABLE_SESSION_SECONDS:
            delay = initial_delay
        log.warning("MQTT-разрыв: %s — переподключение через %.1f с", error, delay)
        await sleep(delay)
        delay = min(delay * 2, max_delay)


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
