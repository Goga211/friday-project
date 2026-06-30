"""Настройки шины из окружения/.env (префикс CHRISTOPHER_)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BusSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHRISTOPHER_",
        env_file=".env",
        extra="ignore",
    )

    broker_host: str = "localhost"
    broker_port: int = 1883

    # mTLS (для боевого/защищённого режима)
    tls: bool = False
    tls_ca: str | None = None
    tls_cert: str | None = None
    tls_key: str | None = None

    # Идентификатор устройства (для агента). Пусто → desktop-<hostname>.
    device_id: str | None = None

    # Интервал ping от Core к агентам, сек
    ping_interval: int = 15
