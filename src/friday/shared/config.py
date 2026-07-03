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
    # Человеческое имя устройства («ноутбук», «пк») — для команд вида «открой на ноутбуке»
    device_alias: str | None = None
    # MAC для Wake-on-LAN. Пусто → автодетект (uuid.getnode); задать, если детект врёт
    device_mac: str | None = None

    # Wake-on-LAN: куда слать магический пакет
    wol_broadcast: str = "255.255.255.255"
    wol_port: int = 9

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
    # Сколько последних реплик диалога держать в контексте мозга (user+assistant вместе).
    # 50 ≈ +1200 вх. токенов/запрос к окну 20 — с prompt caching единицы % бюджета (§5.1)
    llm_history_max_messages: int = 50

    # Веб-чат (зачаток HUD): адрес HTTP-сервера
    hud_host: str = "127.0.0.1"
    hud_port: int = 8010
    # Bearer-токен REST API HUD (/api/* — для iPhone Shortcuts). Не задан → API отключён
    hud_token: str | None = None
    # Таймаут ожидания ответа мозга в REST API HUD, сек
    hud_api_timeout: float = 60.0

    # Push-уведомления на телефон: полный URL приватного ntfy-топика
    # (например https://ntfy.sh/friday-<секрет>). Не задан → инструмент notify_phone отключён
    push_url: str | None = None

    # Файл аудита действий (SQLite)
    audit_db: str = "friday.db"

    # Персистентное хранилище задач планировщика (SQLite); переживает перезагрузку Hub'а
    scheduler_db: str = "friday-jobs.db"
