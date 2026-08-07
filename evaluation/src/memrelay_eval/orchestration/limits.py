"""Bounded local concurrency for disposable attempt workers."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock

from memrelay_eval.domain.errors import ProcessLimitError


class AttemptProcessLimiter:
    """Reject excess or duplicate active attempts without queuing shared workers."""

    def __init__(self, maximum_active_attempts: int) -> None:
        if maximum_active_attempts < 1:
            raise ProcessLimitError("process_limit_invalid")
        self._maximum = maximum_active_attempts
        self._active: set[str] = set()
        self._lock = Lock()

    @contextmanager
    def lease(self, attempt_id: str):
        with self._lock:
            if attempt_id in self._active:
                raise ProcessLimitError("attempt_process_already_active")
            if len(self._active) >= self._maximum:
                raise ProcessLimitError("attempt_process_limit_reached")
            self._active.add(attempt_id)
        try:
            yield
        finally:
            with self._lock:
                self._active.remove(attempt_id)
