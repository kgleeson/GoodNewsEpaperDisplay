import threading
import time

from epaper_goodnews.job_manager import JobManager


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_new_job_cancels_the_running_one_and_waits_for_it():
    manager = JobManager()
    events: list[str] = []
    first_started, release = threading.Event(), threading.Event()

    def first(cancel_event):
        first_started.set()
        release.wait(10)  # a blocking API call: it can't react to the flag until it returns
        events.append(f"first saw cancel: {cancel_event.is_set()}")

    def second(cancel_event):
        events.append("second started")

    manager.start(first)
    assert first_started.wait(5)
    manager.start(second)  # returns at once; it must not wait for the first job

    time.sleep(0.2)
    assert events == []  # the second job is queued, not running alongside the first
    assert manager.is_running()
    release.set()

    assert wait_until(lambda: not manager.is_running())
    assert events == ["first saw cancel: True", "second started"]


def test_is_running_stays_true_while_the_replacement_job_runs():
    manager = JobManager()
    release = threading.Event()
    second_started = threading.Event()

    def second(cancel_event):
        second_started.set()
        release.wait(5)

    manager.start(lambda cancel_event: cancel_event.wait(5))
    manager.start(second)
    assert second_started.wait(5)
    time.sleep(0.05)  # let the first job's thread finish its clean-up
    assert manager.is_running() is True
    release.set()
    assert wait_until(lambda: not manager.is_running())


def test_job_replaced_before_it_starts_never_runs():
    manager = JobManager()
    ran: list[str] = []
    release = threading.Event()

    manager.start(lambda cancel_event: release.wait(5))
    manager.start(lambda cancel_event: ran.append("second"))
    manager.start(lambda cancel_event: ran.append("third"))
    release.set()

    assert wait_until(lambda: not manager.is_running())
    assert ran == ["third"]


def test_cancel_sets_the_running_jobs_flag():
    manager = JobManager()
    seen: list[bool] = []
    manager.start(lambda cancel_event: seen.append(cancel_event.wait(5)))
    manager.cancel()
    assert wait_until(lambda: not manager.is_running())
    assert seen == [True]
