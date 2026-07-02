"""Настройки шины из окружения/.env (префикс FRIDAY_)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BusSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FRIDAY_",
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

    # Авто-переподключение к брокеру при разрыве: стартовая и потолочная задержка backoff, сек
    reconnect_initial_delay: float = 1.0
    reconnect_max_delay: float = 60.0

    # Таймаут ожидания ответа устройства на команду, сек
    command_timeout: float = 30.0

    # Мозг (Claude). API-ключ берётся из ANTHROPIC_API_KEY (стандарт SDK).
    # По умолчанию Haiku — дёшево для базовых команд; сложное можно поднять до Sonnet/Opus.
    llm_model: str = "claude-haiku-4-5"
    llm_max_tokens: int = 2048
    llm_max_iterations: int = 8
    # Сколько последних реплик диалога держать в контексте мозга (user+assistant вместе)
    llm_history_max_messages: int = 20

    # Веб-чат (зачаток HUD): адрес HTTP-сервера
    hud_host: str = "127.0.0.1"
    hud_port: int = 8010

    # Файл аудита действий (SQLite)
    audit_db: str = "friday.db"

    # Персистентное хранилище задач планировщика (SQLite); переживает перезагрузку Hub'а
    scheduler_db: str = "friday-jobs.db"
