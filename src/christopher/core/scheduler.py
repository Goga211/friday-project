"""Планировщик отложенных и повторяющихся действий (Hub, локально, без ИИ).

Claude разово разбирает фразу («выключи кондей через час») в вызов schedule_action; дальше
это обычный код: APScheduler хранит задачу в SQLite и сам отсчитывает время, при срабатывании
публикует команду в MQTT. Планировщик always-on (Core 24/7): задачи переживают перезагрузку —
persistent job store перечитывается при старте, без интернета в момент срабатывания.

Функция срабатывания `_fire` — модульного уровня (её путь пиклится в SQLite job store),
а актуальную публикацию она берёт из модульного _dispatch, который Core ставит при старте.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger("christopher.scheduler")

# (target_device, action, params) -> None. Ставится Core при старте, переживает reload задач.
DispatchFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]
_dispatch: DispatchFn | None = None

_MISFIRE_GRACE = 3600  # сек: если Core лежал в момент срабатывания — выполнить в течение часа


async def _fire(target: str, action: str, params: dict[str, Any]) -> None:
    """Срабатывание задачи: опубликовать команду устройству. Вызывается APScheduler."""
    if _dispatch is None:
        log.error("scheduler: срабатывание без dispatch (target=%s action=%s)", target, action)
        return
    log.info("scheduler: срабатывание %s → %s(%s)", target, action, params)
    await _dispatch(target, action, dict(params))


class ActionScheduler:
    def __init__(self, db_path: str, dispatch: DispatchFn) -> None:
        global _dispatch
        _dispatch = dispatch
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")},
            timezone="UTC",
        )

    def start(self) -> None:
        self._scheduler.start()
        log.info("scheduler запущен, задач в очереди: %d", len(self._scheduler.get_jobs()))

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def schedule_once(
        self, target: str, action: str, params: dict[str, Any], run_at: datetime
    ) -> str:
        job = self._scheduler.add_job(
            _fire,
            "date",
            run_date=run_at,
            kwargs={"target": target, "action": action, "params": params},
            misfire_grace_time=_MISFIRE_GRACE,
        )
        return str(job.id)

    def schedule_cron(self, target: str, action: str, params: dict[str, Any], cron: str) -> str:
        job = self._scheduler.add_job(
            _fire,
            CronTrigger.from_crontab(cron, timezone="UTC"),
            kwargs={"target": target, "action": action, "params": params},
            misfire_grace_time=_MISFIRE_GRACE,
        )
        return str(job.id)

    def cancel(self, job_id: str) -> bool:
        if self._scheduler.get_job(job_id) is None:
            return False
        self._scheduler.remove_job(job_id)
        return True

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs = []
        for job in self._scheduler.get_jobs():
            kwargs = job.kwargs or {}
            next_run = getattr(job, "next_run_time", None)
            jobs.append(
                {
                    "id": job.id,
                    "target": kwargs.get("target"),
                    "action": kwargs.get("action"),
                    "params": kwargs.get("params"),
                    "next_run": next_run.isoformat() if next_run else None,
                }
            )
        return jobs


def parse_when(delay_seconds: int | None, at: str | None) -> datetime:
    """Вычислить момент запуска из относительной задержки или ISO-времени. Всегда UTC."""
    if delay_seconds is not None:
        return datetime.now(UTC) + timedelta(seconds=int(delay_seconds))
    if at:
        parsed = datetime.fromisoformat(at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    raise ValueError("нужно указать delay_seconds или at (ISO-время)")
