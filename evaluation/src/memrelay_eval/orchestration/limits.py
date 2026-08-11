"""Bounded local concurrency for disposable attempt workers."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from threading import Lock

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import ProcessLimitError, StageControlError


def stage_envelope_digest(price_table_sha256: str, limits_sha256: str) -> str:
    """Bind the sealed price table and stage limits into one scoping digest.

    The digest is what an operator or scheduler authorization is scoped to, so an
    envelope change (new price table or new limit set) invalidates every prior
    authorization instead of silently expanding a paid budget.
    """

    return canonical_digest(
        {
            "artifact_type": "stage_envelope",
            "schema_version": "1.0.0",
            "price_table_sha256": price_table_sha256,
            "limits_sha256": limits_sha256,
        }
    )


def stage_headroom_status(
    consumed: Mapping[str, int | float], limits: Mapping[str, int | float]
) -> dict[str, object]:
    """Project outcome-blind headroom for each sealed limit dimension.

    ``exhausted`` is reported when any dimension has no remaining headroom; the
    monitor uses it to pause new work. This never mutates evidence and never
    reveals treatment content.
    """

    if set(consumed) != set(limits):
        raise StageControlError(
            "stage_headroom_dimension_mismatch",
            (*sorted(set(limits) - set(consumed)), *sorted(set(consumed) - set(limits))),
        )
    dimensions: dict[str, object] = {}
    exhausted = False
    for name in sorted(limits):
        cap = limits[name]
        used = consumed[name]
        if (
            isinstance(cap, bool)
            or isinstance(used, bool)
            or not isinstance(cap, (int, float))
            or not isinstance(used, (int, float))
        ):
            raise StageControlError("stage_headroom_value_not_numeric", (name,))
        if cap < 0 or used < 0:
            raise StageControlError("stage_headroom_negative", (name,))
        remaining = cap - used
        depleted = remaining <= 0
        exhausted = exhausted or depleted
        dimensions[name] = {
            "limit": cap,
            "consumed": used,
            "remaining": remaining if remaining > 0 else 0,
            "exhausted": depleted,
        }
    return {"dimensions": dimensions, "exhausted": exhausted}


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
