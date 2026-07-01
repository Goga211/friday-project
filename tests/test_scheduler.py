"""Тесты планировщика действий (APScheduler + SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from christopher.core import scheduler as scheduler_module
from christopher.core.scheduler import ActionScheduler, parse_when


def test_parse_when_delay_seconds() -> None:
    before = datetime.now(UTC)
    when = parse_when(60, None)
    assert (when - before).total_seconds() >= 59


def test_parse_when_iso_time() -> None:
    when = parse_when(None, "2030-01-01T12:00:00")
    assert when.year == 2030
    assert when.tzinfo is not None  # naive → UTC


def test_parse_when_requires_argument() -> None:
    with pytest.raises(ValueError):
        parse_when(None, None)


@pytest.mark.asyncio
async def test_schedule_list_cancel(tmp_path: Path) -> None:
    async def _dispatch(target: str, action: str, params: dict) -> None:  # pragma: no cover
        pass

    sched = ActionScheduler(str(tmp_path / "jobs.db"), _dispatch)
    sched.start()
    try:
        run_at = datetime.now(UTC) + timedelta(hours=1)
        job_id = sched.schedule_once("desktop-x", "notify", {"title": "т"}, run_at)

        jobs = sched.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["id"] == job_id
        assert jobs[0]["target"] == "desktop-x"
        assert jobs[0]["action"] == "notify"

        assert sched.cancel(job_id) is True
        assert sched.list_jobs() == []
        assert sched.cancel("no-such-id") is False
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_cron_job_scheduled(tmp_path: Path) -> None:
    async def _dispatch(target: str, action: str, params: dict) -> None:  # pragma: no cover
        pass

    sched = ActionScheduler(str(tmp_path / "jobs.db"), _dispatch)
    sched.start()
    try:
        job_id = sched.schedule_cron("desktop-x", "notify", {"title": "утро"}, "0 9 * * *")
        assert any(j["id"] == job_id for j in sched.list_jobs())
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_fire_calls_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict]] = []

    async def _dispatch(target: str, action: str, params: dict) -> None:
        calls.append((target, action, params))

    monkeypatch.setattr(scheduler_module, "_dispatch", _dispatch)
    await scheduler_module._fire("desktop-x", "notify", {"k": "v"})
    assert calls == [("desktop-x", "notify", {"k": "v"})]
