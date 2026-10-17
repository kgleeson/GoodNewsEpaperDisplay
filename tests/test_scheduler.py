from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import apscheduler.schedulers.base as base
import pytest

from epaper_goodnews.config import SchedulerConfig
from epaper_goodnews.scheduler import SchedulerService, missed_todays_run

DUBLIN = ZoneInfo("Europe/Dublin")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=DUBLIN)


@pytest.mark.parametrize(
    ("now", "last_success", "expected"),
    [
        ("2026-10-17T07:59:00", None, False),  # before today's slot
        ("2026-10-17T10:00:00", None, True),  # never succeeded
        ("2026-10-17T10:00:00", "2026-10-16T08:01:00", True),  # yesterday only
        ("2026-10-17T10:00:00", "2026-10-17T08:02:00", False),  # done today
        ("2026-10-17T10:00:00", "2026-10-17T07:30:00", True),  # manual run before slot
    ],
)
def test_missed_todays_run(now, last_success, expected):
    last = datetime.fromisoformat(last_success) if last_success else None  # naive, like metadata
    assert missed_todays_run(at(now), last, 8, 0) is expected


def test_daily_job_runs_however_late():
    svc = SchedulerService(SchedulerConfig(hour=8, minute=0, timezone="Europe/Dublin"))
    svc._scheduler.start(paused=True)
    try:
        svc.schedule_daily(lambda: None)
        job = svc._scheduler.get_job("daily-update")
        assert job.misfire_grace_time is None
        assert job.coalesce is True
    finally:
        svc._scheduler.shutdown(wait=False)


def _lateness_after_firing(run_local: datetime) -> timedelta:
    """Replay APScheduler's wait computation right after the job fires at run_local."""
    fixed = run_local.replace(microsecond=1000)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    svc = SchedulerService(SchedulerConfig(hour=8, minute=0, timezone="Europe/Dublin"))
    scheduler = svc._scheduler
    scheduler.start(paused=True)
    try:
        svc.schedule_daily(lambda: None)
        scheduler.modify_job("daily-update", next_run_time=run_local)
        scheduler.state = base.STATE_RUNNING
        with mock.patch.object(base, "datetime", FakeDateTime):
            wait_seconds = scheduler._process_jobs()
        next_run = scheduler.get_job("daily-update").next_run_time
        # Event.wait() sleeps on the monotonic clock, so the wake time is now + wait.
        wake = fixed.astimezone(timezone.utc) + timedelta(seconds=wait_seconds)
        return wake - next_run.astimezone(timezone.utc)
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.parametrize(
    "fired",
    ["2026-03-28T08:00:00", "2027-03-27T08:00:00", "2026-10-24T08:00:00", "2026-06-10T08:00:00"],
)
def test_no_dst_lateness(fired):
    # 2026-03-29 fired an hour late in production with a ZoneInfo-zoned scheduler.
    assert abs(_lateness_after_firing(at(fired))) < timedelta(seconds=1)
