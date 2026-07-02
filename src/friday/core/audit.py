"""Аудит действий: каждое выполнение инструмента пишется в SQLite (кто/что/когда/результат)."""

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
        self._conn.commit()

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
