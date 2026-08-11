"""Bounded local concurrency for disposable attempt workers."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import ProcessLimitError, StageControlError


@dataclass(frozen=True, slots=True)
class PrimaryModelStageLimits:
    """The fixed paid-call envelope for the 512-unit confirmatory primary stage."""

    ai_credit_cap: float
    usd_cap: float
    task_class_active_seconds: Mapping[str, float]
    task_agent_token_cap: int = 128_000_000
    framework_input_token_cap: int = 24_000_000
    framework_output_token_cap: int = 8_000_000
    elapsed_days_cap: int = 10
    concurrency_cap: int = 4

    def __post_init__(self) -> None:
        if (
            self.task_agent_token_cap != 128_000_000
            or self.framework_input_token_cap != 24_000_000
            or self.framework_output_token_cap != 8_000_000
            or self.elapsed_days_cap != 10
            or self.concurrency_cap != 4
        ):
            raise StageControlError("primary_limits_relaxation_forbidden")
        _validate_paid_caps(
            self.ai_credit_cap, self.usd_cap, self.task_class_active_seconds, "primary"
        )
        object.__setattr__(
            self,
            "task_class_active_seconds",
            dict(sorted(self.task_class_active_seconds.items())),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "artifact_type": "primary_model_stage_limits",
            "task_agent_token_cap": self.task_agent_token_cap,
            "framework_input_token_cap": self.framework_input_token_cap,
            "framework_output_token_cap": self.framework_output_token_cap,
            "ai_credit_cap": self.ai_credit_cap,
            "usd_cap": self.usd_cap,
            "task_class_active_seconds": dict(self.task_class_active_seconds),
            "elapsed_days_cap": self.elapsed_days_cap,
            "concurrency_cap": self.concurrency_cap,
        }


@dataclass(frozen=True, slots=True)
class SecondaryModelStageLimits:
    """The 48M aggregate envelope with immutable, non-transferable M1/M2 subcaps."""

    ai_credit_cap: float
    usd_cap: float
    task_class_active_seconds: Mapping[str, float]
    task_agent_token_cap: int = 48_000_000
    framework_input_token_cap: int = 9_000_000
    framework_output_token_cap: int = 3_000_000
    elapsed_days_cap: int = 5
    per_role_task_agent_token_cap: int = 24_000_000
    per_role_framework_input_token_cap: int = 4_500_000
    per_role_framework_output_token_cap: int = 1_500_000

    def __post_init__(self) -> None:
        if (
            self.task_agent_token_cap != 48_000_000
            or self.framework_input_token_cap != 9_000_000
            or self.framework_output_token_cap != 3_000_000
            or self.elapsed_days_cap != 5
            or self.per_role_task_agent_token_cap != 24_000_000
            or self.per_role_framework_input_token_cap != 4_500_000
            or self.per_role_framework_output_token_cap != 1_500_000
        ):
            raise StageControlError("secondary_limits_relaxation_forbidden")
        _validate_paid_caps(
            self.ai_credit_cap, self.usd_cap, self.task_class_active_seconds, "secondary"
        )
        object.__setattr__(
            self,
            "task_class_active_seconds",
            dict(sorted(self.task_class_active_seconds.items())),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def for_role(self, role: str) -> dict[str, int]:
        """Return one fixed subcap; an unavailable role cannot donate its allocation."""

        if role not in {"M1", "M2"}:
            raise StageControlError("secondary_role_unknown", (role,))
        return {
            "task_agent_token_cap": self.per_role_task_agent_token_cap,
            "framework_input_token_cap": self.per_role_framework_input_token_cap,
            "framework_output_token_cap": self.per_role_framework_output_token_cap,
        }

    def to_document(self) -> dict[str, object]:
        return {
            "artifact_type": "secondary_model_stage_limits",
            "task_agent_token_cap": self.task_agent_token_cap,
            "framework_input_token_cap": self.framework_input_token_cap,
            "framework_output_token_cap": self.framework_output_token_cap,
            "ai_credit_cap": self.ai_credit_cap,
            "usd_cap": self.usd_cap,
            "task_class_active_seconds": dict(self.task_class_active_seconds),
            "elapsed_days_cap": self.elapsed_days_cap,
            "per_role_task_agent_token_cap": self.per_role_task_agent_token_cap,
            "per_role_framework_input_token_cap": self.per_role_framework_input_token_cap,
            "per_role_framework_output_token_cap": self.per_role_framework_output_token_cap,
        }


def _validate_paid_caps(
    ai_credit_cap: float,
    usd_cap: float,
    task_class_active_seconds: Mapping[str, float],
    stage: str,
) -> None:
    if (
        isinstance(ai_credit_cap, bool)
        or isinstance(usd_cap, bool)
        or not isinstance(ai_credit_cap, (int, float))
        or not isinstance(usd_cap, (int, float))
        or ai_credit_cap <= 0
        or usd_cap <= 0
        or not task_class_active_seconds
        or any(
            not isinstance(name, str)
            or not name
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value <= 0
            for name, value in task_class_active_seconds.items()
        )
    ):
        raise StageControlError(f"{stage}_limits_invalid")


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
