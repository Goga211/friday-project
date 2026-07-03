"""Долгосрочная память: дистиллированные факты в SQLite + выбор релевантных моделью.

Поиск — LLM-as-retriever: все активные факты отдаются под-вызовом дешёвой модели
(Haiku), она выбирает релевантные запросу. Синонимы и парафраз решены по построению
(«что с интернетом» находит факт про роутер) — без FTS/стемминга/эмбеддингов.
Потолок масштаба (~2–3 тыс. фактов) далеко; тогда добавится префильтр кандидатов.
Выбор делает ОТДЕЛЬНЫЙ вызов, не основной мозг: иначе весь список фактов осел бы
в истории диалога. Дизайн — Obsidian «Пятница — дизайн recall (долгосрочная память)».
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("friday.memory")

KINDS = ("fact", "preference", "decision")

_SELECT_LIMIT = 5
_SELECT_MAX_TOKENS = 128

_SELECT_SYSTEM = (
    "Ты — поиск по долгосрочной памяти ассистента. Тебе дан пронумерованный список "
    "фактов и запрос. Выбери факты, релевантные запросу по СМЫСЛУ (учитывай синонимы "
    "и парафраз). Ответь ТОЛЬКО номерами выбранных фактов через запятую. Если ничего "
    "не подходит — ответь одним словом: НЕТ."
)


@dataclass(frozen=True)
class Fact:
    """Один факт долгосрочной памяти."""

    id: int
    text: str
    kind: str
    created_at: str


class MemoryStore:
    """Хранилище фактов (таблица memory_facts в общей SQLite Core)."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_facts(
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT NOT NULL,
                kind    TEXT NOT NULL,
                source  TEXT NOT NULL,
                text    TEXT NOT NULL,
                active  INTEGER NOT NULL DEFAULT 1
            )
            """)
        self._conn.commit()

    def remember(self, text: str, kind: str = "fact", source: str = "explicit") -> int:
        """Сохранить факт, вернуть его id."""
        cursor = self._conn.execute(
            "INSERT INTO memory_facts(ts, kind, source, text) VALUES(?,?,?,?)",
            (datetime.now(UTC).isoformat(), kind, source, text),
        )
        self._conn.commit()
        return int(cursor.lastrowid or 0)

    def active_facts(self) -> list[Fact]:
        """Все активные факты в порядке записи (id — стабильные номера для селектора)."""
        rows = self._conn.execute(
            "SELECT id, text, kind, ts FROM memory_facts WHERE active = 1 ORDER BY id"
        ).fetchall()
        return [Fact(id=r[0], text=r[1], kind=r[2], created_at=r[3]) for r in rows]

    def forget(self, ids: list[int]) -> int:
        """Мягко забыть факты (active=0, физически не удаляем). Вернуть число забытых."""
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        cursor = self._conn.execute(
            f"UPDATE memory_facts SET active = 0 WHERE active = 1 AND id IN ({marks})",  # noqa: S608 — плейсхолдеры, не данные
            ids,
        )
        self._conn.commit()
        return int(cursor.rowcount)

    def close(self) -> None:
        self._conn.close()


async def select_relevant(
    client: Any,
    model: str,
    facts: list[Fact],
    query: str,
    limit: int = _SELECT_LIMIT,
) -> list[Fact]:
    """Выбрать релевантные запросу факты под-вызовом дешёвой модели.

    Блок фактов стабилен между вызовами → на нём cache_control (префикс system+факты
    кэшируется; на маленькой базе кэш Haiku не включится — там и так копейки).
    """
    if not facts:
        return []
    listing = "\n".join(f"{f.id}. {f.text}" for f in facts)
    response = await client.messages.create(
        model=model,
        max_tokens=_SELECT_MAX_TOKENS,
        system=_SELECT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Факты:\n{listing}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": f"Запрос: {query}"},
                ],
            }
        ],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    picked = {int(number) for number in re.findall(r"\d+", text)}
    by_id = {fact.id: fact for fact in facts}
    selected = [by_id[fact_id] for fact_id in sorted(picked) if fact_id in by_id]
    log.info("recall: %d фактов в базе, выбрано %d", len(facts), len(selected))
    return selected[:limit]
