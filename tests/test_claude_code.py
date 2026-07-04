"""Тесты делегирования в Claude Code: режимы, headless-поток с событием, валидация.

Вместо настоящего Claude Code — фейковый скрипт `claude` в PATH (без сети и подписки).
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from friday.agents.desktop import claude_code
from friday.shared.agent import publish_events
from friday.shared.protocol import Event
from friday.shared.topics import event_topic


@pytest.fixture(autouse=True)
def _fresh_events_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Свежая очередь на каждый тест: у pytest-asyncio каждый тест — новый event loop,
    а asyncio.Queue привязывается к loop'у первого использования (в проде loop один)."""
    monkeypatch.setattr(claude_code, "EVENTS", asyncio.Queue())


def _install_fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, py_body: str) -> None:
    """Положить исполняемый скрипт `claude` в начало PATH.

    Тело — Python (кросс-платформенно). На Windows sh-скрипт без расширения
    невидим для shutil.which — нашёлся бы НАСТОЯЩИЙ claude, поэтому лаунчер
    здесь .cmd, а на POSIX — sh-обёртка; оба зовут интерпретатор текущего venv.
    """
    impl = tmp_path / "fake_claude.py"
    impl.write_text(py_body, encoding="utf-8")
    if sys.platform == "win32":
        launcher = tmp_path / "claude.cmd"
        # -X utf8: вывод в pipe в UTF-8 (агент декодирует stdout как UTF-8)
        launcher.write_text(f'@"{sys.executable}" -X utf8 "{impl}" %*\n', encoding="utf-8")
    else:
        launcher = tmp_path / "claude"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{impl}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")


async def _next_event() -> tuple[str, dict[str, Any]]:
    return await asyncio.wait_for(claude_code.EVENTS.get(), timeout=5.0)


# --- валидация параметров ---


async def test_requires_task() -> None:
    with pytest.raises(ValueError, match="task"):
        await claude_code.run_claude_task({})


async def test_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        await claude_code.run_claude_task({"task": "x", "mode": "чересчур"})


async def test_rejects_missing_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_claude(tmp_path, monkeypatch, "raise SystemExit(0)")
    with pytest.raises(ValueError, match="cwd"):
        await claude_code.run_claude_task({"task": "x", "cwd": str(tmp_path / "нет-такой")})


async def test_errors_without_claude_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(RuntimeError, match="claude"):
        await claude_code.run_claude_task({"task": "x"})


# --- headless: фоновая задача и событие завершения ---


async def test_headless_success_puts_done_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_claude(
        tmp_path,
        monkeypatch,
        'print(\'{"result": "готово: 42", "is_error": false}\')',
    )
    out = await claude_code.run_claude_task({"task": "посчитай", "mode": "headless"})
    assert out["mode"] == "headless"
    assert out["started"] is True

    event_type, data = await _next_event()
    assert event_type == claude_code.TASK_DONE_EVENT
    assert data["ok"] is True
    assert data["result"] == "готово: 42"
    assert data["task"] == "посчитай"


async def test_headless_failure_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_claude(
        tmp_path,
        monkeypatch,
        'import sys\nprint("всё сломалось", file=sys.stderr)\nraise SystemExit(3)',
    )
    await claude_code.run_claude_task({"task": "x", "mode": "headless"})
    _event_type, data = await _next_event()
    assert data["ok"] is False
    assert "кодом 3" in data["result"]
    assert "всё сломалось" in data["result"]


async def test_headless_timeout_kills_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_claude(tmp_path, monkeypatch, "import time\ntime.sleep(30)")
    monkeypatch.setenv("FRIDAY_CLAUDE_TASK_TIMEOUT", "0.3")
    await claude_code.run_claude_task({"task": "x", "mode": "headless"})
    _event_type, data = await _next_event()
    assert data["ok"] is False
    assert "таймаут" in data["result"]


async def test_headless_non_json_output_kept_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_claude(tmp_path, monkeypatch, 'print("просто текст")')
    await claude_code.run_claude_task({"task": "x", "mode": "headless"})
    _event_type, data = await _next_event()
    assert data["ok"] is True
    assert data["result"] == "просто текст"


# --- выбор режима ---


async def test_auto_prefers_visible_when_user_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_claude(tmp_path, monkeypatch, "raise SystemExit(0)")

    async def _active() -> float | None:
        return 5.0

    spawned: list[tuple[str, str | None]] = []

    async def _fake_spawn(claude: str, task: str, cwd: str | None) -> str:
        spawned.append((task, cwd))
        return "fake-term"

    monkeypatch.setattr(claude_code, "user_idle_seconds", _active)
    monkeypatch.setattr(claude_code, "_spawn_visible", _fake_spawn)

    out = await claude_code.run_claude_task({"task": "поправь код", "cwd": str(tmp_path)})
    assert out["mode"] == "visible"
    assert out["terminal"] == "fake-term"
    assert spawned == [("поправь код", str(tmp_path))]


async def test_auto_falls_back_to_headless_when_idle_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_claude(tmp_path, monkeypatch, 'print(\'{"result": "ok"}\')')

    async def _unknown() -> float | None:
        return None

    monkeypatch.setattr(claude_code, "user_idle_seconds", _unknown)
    out = await claude_code.run_claude_task({"task": "x"})
    assert out["mode"] == "headless"
    await _next_event()  # дочистить очередь за фоновой задачей


# --- публикация событий из очереди на шину (shared/agent.py) ---


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    async def publish_model(self, topic: str, model: Any, qos: int = 1, retain: bool = False):
        self.published.append((topic, model))


async def test_publish_events_wraps_queue_items_into_events() -> None:
    bus = _FakeBus()
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    task = asyncio.create_task(publish_events(bus, "desktop-pc", queue))  # type: ignore[arg-type]
    await queue.put(("claude_task_done", {"ok": True, "result": "готово"}))
    try:
        await asyncio.sleep(0.05)  # publisher успевает забрать элемент из очереди
        assert bus.published, "событие не опубликовано"
        topic, model = bus.published[0]
        assert topic == event_topic("desktop-pc", "claude_task_done")
        assert isinstance(model, Event)
        assert model.source == "desktop-pc"
        assert model.data["result"] == "готово"
    finally:
        task.cancel()
