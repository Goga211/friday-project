"""Аудит и лог диалога: SQLite.

- audit — каждое выполнение инструмента (кто/что/когда/результат);
- dialog — реплики пользователя и ассистента (история переживает рестарт Core:
  последние N реплик подгружаются в контекст мозга при старте).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


class AuditLog:
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS audit(
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT NOT NULL,
                device  TEXT,
                action  TEXT,
                params  TEXT,
                ok      INTEGER,
                error   TEXT
            )
            """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS dialog(
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                ts    TEXT NOT NULL,
                role  TEXT NOT NULL,
                text  TEXT NOT NULL
            )
            """)
        self._conn.commit()

    def record_dialog(self, role: str, text: str) -> None:
        """Записать реплику диалога (role: user | assistant)."""
        self._conn.execute(
            "INSERT INTO dialog(ts, role, text) VALUES(?,?,?)",
            (datetime.now(UTC).isoformat(), role, text),
        )
        self._conn.commit()

    def recent_dialog(self, limit: int) -> list[tuple[str, str]]:
        """Последние реплики диалога в хронологическом порядке: [(role, text), …]."""
        rows = self._conn.execute(
            "SELECT role, text FROM dialog ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [(role, text) for role, text in reversed(rows)]

    def record(
        self,
        *,
        device: str,
        action: str,
        params: dict[str, Any],
        ok: bool,
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO audit(ts, device, action, params, ok, error) VALUES(?,?,?,?,?,?)",
            (
                datetime.now(UTC).isoformat(),
                device,
                action,
                json.dumps(params, ensure_ascii=False),
                int(ok),
                error,
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
