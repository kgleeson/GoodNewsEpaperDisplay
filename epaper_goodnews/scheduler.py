from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .config import SchedulerConfig

LOGGER = logging.getLogger(__name__)

# Delay before the startup catch-up check, so NTP has time to correct the clock
# (the Pi has no RTC and boots with a stale time).
CATCH_UP_DELAY_SECONDS = 300


def missed_todays_run(now: datetime, last_success: datetime | None, hour: int, minute: int) -> bool:
    """True if today's scheduled time has passed and there is no success since it.

    `now` must be timezone-aware in the schedule's zone. A naive `last_success` is
    taken to be in that same zone.
    """
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < scheduled:
        return False
    if last_success is None:
        return True
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=now.tzinfo)
    return last_success < scheduled


class SchedulerService:
    def __init__(self, config: SchedulerConfig) -> None:
        self._config = config
        self._timezone = ZoneInfo(config.timezone)
        # The scheduler itself runs in UTC: APScheduler 3.10 computes its wait as a
        # naive wall-clock difference in the scheduler's zone, which fires an hour
        # late on the spring DST change. The trigger keeps the local zone.
        # misfire_grace_time=None: a late wake-up (e.g. after an NTP clock step)
        # still runs the job instead of dropping the day.
        self._scheduler = BackgroundScheduler(
            timezone=timezone.utc,
            job_defaults={"misfire_grace_time": None, "coalesce": True},
        )
        self._scheduler.add_listener(self._on_missed, EVENT_JOB_MISSED)
        self._job = None

    @staticmethod
    def _on_missed(event) -> None:
        LOGGER.error(
            "Scheduled job %s missed its run time %s", event.job_id, event.scheduled_run_time
        )

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
            LOGGER.info("Scheduler started; daily job in timezone %s", self._config.timezone)

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            LOGGER.info("Scheduler stopped")

    def schedule_daily(self, func: Callable[[], None]) -> None:
        if self._job:
            self._job.remove()

        trigger = CronTrigger(
            hour=self._config.hour,
            minute=self._config.minute,
            timezone=self._timezone,
        )
        self._job = self._scheduler.add_job(func, trigger=trigger, id="daily-update")
        LOGGER.info(
            "Scheduled daily job at %02d:%02d %s",
            self._config.hour,
            self._config.minute,
            self._config.timezone,
        )

    def schedule_catch_up(
        self,
        func: Callable[[], None],
        last_success: Callable[[], datetime | None],
        delay_seconds: int = CATCH_UP_DELAY_SECONDS,
    ) -> None:
        """Run `func` once, shortly after startup, if today's run was missed."""

        def check() -> None:
            now = datetime.now(self._timezone)
            if missed_todays_run(now, last_success(), self._config.hour, self._config.minute):
                LOGGER.warning("No successful run since today's scheduled time; catching up")
                func()

        run_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        self._scheduler.add_job(check, trigger=DateTrigger(run_date=run_at), id="catch-up")

    @property
    def next_run(self) -> str | None:
        if self._job:
            next_run = self._job.next_run_time
            return next_run.astimezone(self._timezone).isoformat() if next_run else None
        return None
