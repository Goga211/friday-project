"""Лог диалога в SQLite: запись и восстановление последних реплик."""

from __future__ import annotations

from pathlib import Path

from friday.core.audit import AuditLog


def test_dialog_roundtrip(tmp_path: Path) -> None:
    log = AuditLog(str(tmp_path / "audit.db"))
    try:
        log.record_dialog("user", "открой ютуб")
        log.record_dialog("assistant", "Открыл")
        log.record_dialog("user", "закрой его")
        log.record_dialog("assistant", "Закрыл")

        assert log.recent_dialog(2) == [("user", "закрой его"), ("assistant", "Закрыл")]
        assert log.recent_dialog(10) == [
            ("user", "открой ютуб"),
            ("assistant", "Открыл"),
            ("user", "закрой его"),
            ("assistant", "Закрыл"),
        ]
    finally:
        log.close()


def test_dialog_survives_reopen(tmp_path: Path) -> None:
    """Реплики переживают закрытие/открытие БД — контекст восстановится после рестарта."""
    path = str(tmp_path / "audit.db")
    log = AuditLog(path)
    log.record_dialog("user", "привет")
    log.record_dialog("assistant", "Привет!")
    log.close()

    reopened = AuditLog(path)
    try:
        assert reopened.recent_dialog(10) == [("user", "привет"), ("assistant", "Привет!")]
    finally:
        reopened.close()
