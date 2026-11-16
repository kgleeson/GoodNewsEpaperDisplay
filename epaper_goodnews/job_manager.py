from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Event, Lock, Thread, current_thread

LOGGER = logging.getLogger(__name__)


class JobManager:
    """Runs one job at a time; starting a new job cancels the one in progress.

    Each job gets its own cancel event, so a cancelled job keeps seeing its flag however
    long it takes to reach a checkpoint. The new job waits for the old one to stop before
    it begins, so two pipelines never run side by side.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._thread: Thread | None = None
        self._cancel_event: Event | None = None

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive() and self._cancel_event:
                LOGGER.info("Cancelling current job")
                self._cancel_event.set()

    def _run(
        self,
        target: Callable[[Event], None],
        cancel_event: Event,
        previous: Thread | None,
    ) -> None:
        try:
            while previous is not None and previous.is_alive():
                previous.join(timeout=1)
                if cancel_event.is_set():
                    LOGGER.info("Job cancelled before it started")
                    return
            target(cancel_event)
        except Exception as exc:  # pragma: no cover - runtime safety
            LOGGER.exception("Job raised exception: %s", exc)
        finally:
            with self._lock:
                # A newer job may already have taken over; only clear our own entry.
                if self._thread is current_thread():
                    self._thread = None
                    self._cancel_event = None
            LOGGER.info("Job finished")

    def start(self, target: Callable[[Event], None]) -> bool:
        with self._lock:
            previous = self._thread if self._thread and self._thread.is_alive() else None
            if previous and self._cancel_event:
                LOGGER.info("Existing job running; cancelling it before the new one starts")
                self._cancel_event.set()
            cancel_event = Event()
            thread = Thread(target=self._run, args=(target, cancel_event, previous), daemon=True)
            self._thread = thread
            self._cancel_event = cancel_event
            thread.start()
            LOGGER.info("Started new job thread")
            return True
