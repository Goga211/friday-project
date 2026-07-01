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

    # Таймаут ожидания ответа устройства на команду, сек
    command_timeout: float = 30.0

    # Мозг (Claude). API-ключ берётся из ANTHROPIC_API_KEY (стандарт SDK).
    # По умолчанию Haiku — дёшево для базовых команд; сложное можно поднять до Sonnet/Opus.
    llm_model: str = "claude-haiku-4-5"
    llm_max_tokens: int = 2048
    llm_max_iterations: int = 8

    # Файл аудита действий (SQLite)
    audit_db: str = "christopher.db"

    # Персистентное хранилище задач планировщика (SQLite); переживает перезагрузку Hub'а
    scheduler_db: str = "christopher-jobs.db"
