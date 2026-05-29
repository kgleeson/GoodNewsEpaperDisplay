from __future__ import annotations

import logging
from threading import Event, Lock, Thread
from typing import Callable, Optional

LOGGER = logging.getLogger(__name__)


class JobManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._thread: Optional[Thread] = None
        self._cancel_event = Event()

    @property
    def cancel_event(self) -> Event:
        return self._cancel_event

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                LOGGER.info("Cancelling current job")
                self._cancel_event.set()

    def _run(self, target: Callable[[Event], None]) -> None:
        try:
            target(self._cancel_event)
        except Exception as exc:  # pragma: no cover - runtime safety
            LOGGER.exception("Job raised exception: %s", exc)
        finally:
            self._cancel_event.clear()
            with self._lock:
                self._thread = None
            LOGGER.info("Job finished")

    def start(self, target: Callable[[Event], None]) -> bool:
        thread_to_join: Optional[Thread] = None
        with self._lock:
            if self._thread and self._thread.is_alive():
                LOGGER.info("Existing job running; scheduling cancellation")
                self._cancel_event.set()
                thread_to_join = self._thread
            else:
                self._cancel_event.clear()

        if thread_to_join:
            thread_to_join.join(timeout=5)
            with self._lock:
                self._thread = None
                self._cancel_event.clear()

        with self._lock:
            thread = Thread(target=self._run, args=(target,), daemon=True)
            self._thread = thread
            thread.start()
            LOGGER.info("Started new job thread")
            return True
