"""Загрузка .env в окружение процесса.

pydantic-settings читает `.env` только для СВОИХ префиксных полей (CHRISTOPHER_*). А
непрефиксные переменные — прежде всего `ANTHROPIC_API_KEY`, который Anthropic SDK берёт из
`os.environ` — так в окружение не попадут. Поэтому явно подтягиваем `.env` при старте каждой
точки входа, до создания клиентов и чтения os.getenv.
"""

from __future__ import annotations

from dotenv import load_dotenv


def load_env() -> None:
    """Подтянуть переменные из .env в окружение (ищет .env от текущего каталога вверх)."""
    load_dotenv()
