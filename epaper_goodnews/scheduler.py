from __future__ import annotations

import logging
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from .config import SchedulerConfig

LOGGER = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, config: SchedulerConfig) -> None:
        self._config = config
        self._timezone = ZoneInfo(config.timezone)
        self._scheduler = BackgroundScheduler(timezone=self._timezone)
        self._job = None

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
            LOGGER.info("Scheduler started in timezone %s", self._config.timezone)

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

    @property
    def next_run(self) -> Optional[str]:
        if self._job:
            next_run = self._job.next_run_time
            return next_run.isoformat() if next_run else None
        return None
