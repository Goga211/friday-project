"""Push-уведомления на телефон через ntfy (https://ntfy.sh или свой сервер).

Конфиг — FRIDAY_PUSH_URL: полный URL приватного топика (https://ntfy.sh/friday-<секрет>).
Публикуем JSON-ом в корень сервера (а не заголовками в топик): заголовки HTTP — latin-1,
а сообщения и заголовки у нас русские.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

_TIMEOUT = 10.0


def _split_topic_url(url: str) -> tuple[str, str]:
    """https://ntfy.sh/friday-x → (https://ntfy.sh, friday-x). ValueError на мусоре."""
    parsed = urlparse(url)
    topic = parsed.path.strip("/")
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not topic or "/" in topic:
        raise ValueError(f"FRIDAY_PUSH_URL должен быть вида https://ntfy.sh/<топик>: {url!r}")
    return f"{parsed.scheme}://{parsed.netloc}", topic


async def push_notify(url: str, message: str, title: str | None = None) -> None:
    """Отправить push в ntfy-топик. Ошибки сети/HTTP пробрасываются наверх."""
    server, topic = _split_topic_url(url)
    payload: dict[str, Any] = {"topic": topic, "message": message}
    if title:
        payload["title"] = title
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(server, json=payload)
        response.raise_for_status()
