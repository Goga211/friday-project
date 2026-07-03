"""Настройки агента умного дома из окружения/.env (префикс FRIDAY_HOME_)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class HomeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FRIDAY_HOME_",
        env_file=".env",
        extra="ignore",
    )

    # Реализация контроллера: mock (по умолчанию, железа нет) | ha (Home Assistant)
    controller: str = "mock"

    # Home Assistant: базовый URL и long-lived access token
    # (профиль пользователя HA → Security → Long-lived access tokens)
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str | None = None
    ha_timeout: float = 10.0
