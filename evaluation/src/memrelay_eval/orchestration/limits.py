"""Bounded local concurrency for disposable attempt workers."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from threading import Lock, RLock
from typing import Final

from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.errors import (
    AttemptExecutionClaimDeniedError,
    ProcessLimitError,
    StageControlError,
)
from memrelay_eval.domain.ids import AttemptId, RunId


class CircuitBreakerState(StrEnum):
    """The append-only lifecycle of a frozen local circuit breaker."""

    OPEN = "open"
    TRIPPED = "tripped"
    DRAINING = "draining"
    CLOSED = "closed"


class CircuitBreakerLimit(StrEnum):
    """The resource and integrity dimensions a stage may seal."""

    COPILOT_TOKENS = "copilot_tokens"
    TOOL_CALLS = "tool_calls"
    COPILOT_AI_CREDITS = "copilot_ai_credits"
    FRAMEWORK_INPUT_TOKENS = "framework_input_tokens"
    FRAMEWORK_OUTPUT_TOKENS = "framework_output_tokens"
    FRAMEWORK_USD = "framework_usd"
    ACTIVE_SECONDS = "active_seconds"
    ELAPSED_SECONDS = "elapsed_seconds"
    QUOTA_EVENTS = "quota_events"
    THROTTLE_EVENTS = "throttle_events"
    MODEL_UNAVAILABLE_EVENTS = "model_unavailable_events"
    INFRASTRUCTURE_FAILURES = "infrastructure_failures"
    EVIDENCE_LOSSES = "evidence_losses"


class CircuitBreakerReason(StrEnum):
    """Typed stop reasons; no member implies an automatic fallback."""

    LIMIT_EXCEEDED = "limit_exceeded"
    QUOTA_EXHAUSTED = "quota_exhausted"
    THROTTLED = "throttled"
    MODEL_UNAVAILABLE = "model_unavailable"
    PROVIDER_CONTENTION = "provider_contention"
    INFRASTRUCTURE_FAILURE_RATE = "infrastructure_failure_rate"
    EVIDENCE_LOSS_RATE = "evidence_loss_rate"
    GOVERNANCE_REVOKED = "governance_revoked"
    LOCK_DRIFT = "lock_drift"


_LIMITS: Final[frozenset[str]] = frozenset(limit.value for limit in CircuitBreakerLimit)
_SHA256: Final = frozenset("0123456789abcdef")


def _require_sha256(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256 for character in value)
    ):
        raise StageControlError("circuit_breaker_source_hash_invalid", (field_name,))
    return value


def _require_quantity(value: object, field_name: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise StageControlError("circuit_breaker_quantity_invalid", (field_name,))
    return value


def _normalized_quantities(
    quantities: Mapping[str, int | float], field_name: str
) -> Mapping[str, int | float]:
    normalized: dict[str, int | float] = {}
    for name, value in quantities.items():
        if name not in _LIMITS:
            raise StageControlError("circuit_breaker_limit_unknown", (field_name, name))
        normalized[name] = _require_quantity(value, name)
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, slots=True)
class FrozenLimitEnvelope:
    """A source-hashed, non-transferable limit envelope for one run or stage."""

    scope: str
    source_sha256: str
    limits: Mapping[str, int | float]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, str) or not self.scope:
            raise StageControlError("circuit_breaker_scope_invalid")
        object.__setattr__(self, "source_sha256", _require_sha256(self.source_sha256, "source_sha256"))
        if not self.limits:
            raise StageControlError("circuit_breaker_limits_empty")
        object.__setattr__(self, "limits", _normalized_quantities(self.limits, "limits"))

    def to_document(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "source_sha256": self.source_sha256,
            "limits": dict(self.limits),
        }


@dataclass(frozen=True, slots=True)
class CircuitBreakerRecord:
    """One immutable journal entry that explains a breaker state transition."""

    stage_id: str
    state: CircuitBreakerState
    reason: CircuitBreakerReason | None
    source_hashes: Mapping[str, str]
    observed: Mapping[str, int | float] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    sequence: int = 0
    resume_authorization_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stage_id, str) or not self.stage_id:
            raise StageControlError("circuit_breaker_stage_invalid")
        if self.sequence < 0:
            raise StageControlError("circuit_breaker_sequence_invalid")
        if self.resume_authorization_id is not None and (
            not isinstance(self.resume_authorization_id, str) or not self.resume_authorization_id
        ):
            raise StageControlError("circuit_breaker_resume_authorization_invalid")
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise StageControlError("circuit_breaker_timestamp_invalid")
        source_hashes = {
            key: _require_sha256(value, f"source_hashes.{key}")
            for key, value in self.source_hashes.items()
        }
        if not source_hashes:
            raise StageControlError("circuit_breaker_source_hashes_empty")
        object.__setattr__(self, "source_hashes", MappingProxyType(dict(sorted(source_hashes.items()))))
        object.__setattr__(self, "observed", _normalized_quantities(self.observed, "observed"))

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "circuit_breaker_record",
            "stage_id": self.stage_id,
            "state": self.state.value,
            "reason": self.reason.value if self.reason is not None else None,
            "source_hashes": dict(self.source_hashes),
            "observed": dict(self.observed),
            "at": self.at.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "sequence": self.sequence,
            "resume_authorization_id": self.resume_authorization_id,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def bytes(self) -> bytes:
        return canonical_bytes({**self.to_document(), "digest": self.digest})


@dataclass(frozen=True, slots=True)
class CircuitBreakerAdmission:
    """An immutable reservation receipt created before an attempt can start."""

    attempt_id: AttemptId
    run_id: RunId
    stage_id: str
    reserved: Mapping[str, int | float]
    stage_source_sha256: str
    run_source_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.stage_id, str) or not self.stage_id:
            raise StageControlError("circuit_breaker_stage_invalid")
        object.__setattr__(self, "reserved", _normalized_quantities(self.reserved, "reserved"))
        object.__setattr__(
            self,
            "stage_source_sha256",
            _require_sha256(self.stage_source_sha256, "stage_source_sha256"),
        )
        object.__setattr__(
            self, "run_source_sha256", _require_sha256(self.run_source_sha256, "run_source_sha256")
        )


@dataclass(frozen=True, slots=True)
class CircuitBreakerTerminalEvidence:
    """The retained breaker-side projection for an attempt that was already active."""

    attempt_id: AttemptId
    terminal_classification: str
    exposure_state: str
    cost_quantities: Mapping[str, int | float]
    evidence_refs: tuple[str, ...]
    itt_retained: bool = True

    def __post_init__(self) -> None:
        if not self.terminal_classification or not self.exposure_state:
            raise StageControlError("circuit_breaker_terminal_invalid")
        if self.itt_retained is not True:
            raise StageControlError("circuit_breaker_itt_retention_required")
        object.__setattr__(
            self, "cost_quantities", _normalized_quantities(self.cost_quantities, "cost_quantities")
        )
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class CircuitBreakerDrainPolicy:
    """Frozen handling for attempts that started before admission stopped."""

    allow_active_cancellation: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.allow_active_cancellation, bool):
            raise StageControlError("circuit_breaker_drain_policy_invalid")


@dataclass(frozen=True, slots=True)
class CircuitBreakerResumeAuthorization:
    """An independent, breaker-reason-scoped authorization to reopen admission."""

    authorization_id: str
    authorizer_id: str
    authorizer_role: str
    stage_id: str
    reason: CircuitBreakerReason
    stage_source_sha256: str
    valid_from: datetime
    valid_until: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.authorization_id, str) or not self.authorization_id:
            raise StageControlError("circuit_breaker_resume_authorization_invalid")
        if not isinstance(self.authorizer_id, str) or not self.authorizer_id:
            raise StageControlError("circuit_breaker_resume_authorization_invalid")
        if self.authorizer_role not in {"operator", "scheduler"}:
            raise StageControlError("circuit_breaker_resume_authorization_invalid")
        if not isinstance(self.stage_id, str) or not self.stage_id:
            raise StageControlError("circuit_breaker_resume_authorization_invalid")
        object.__setattr__(
            self,
            "stage_source_sha256",
            _require_sha256(self.stage_source_sha256, "stage_source_sha256"),
        )
        if (
            self.valid_from.tzinfo is None
            or self.valid_from.utcoffset() is None
            or self.valid_until.tzinfo is None
            or self.valid_until.utcoffset() is None
            or self.valid_until <= self.valid_from
        ):
            raise StageControlError("circuit_breaker_resume_authorization_invalid")

    def is_current(self, now: datetime) -> bool:
        return self.valid_from <= now < self.valid_until


class CircuitBreakerAdmissionController:
    """Atomically reserves frozen envelopes and preserves started-attempt evidence.

    Every admission and asynchronous provider signal is serialized by one local
    authority. A rejected start cannot race a trip, and the reservations are never
    released after an execution failure: observed usage may increase them, but no
    attempt can transfer its remaining headroom to another model or provider.
    """

    def __init__(
        self,
        *,
        stage_id: str,
        stage_envelope: FrozenLimitEnvelope,
        run_envelopes: Mapping[RunId, FrozenLimitEnvelope],
        record_sink: Callable[[CircuitBreakerRecord], object] | None = None,
        drain_policy: CircuitBreakerDrainPolicy = CircuitBreakerDrainPolicy(),
        journal: tuple[CircuitBreakerRecord, ...] | None = None,
    ) -> None:
        if not isinstance(stage_id, str) or not stage_id:
            raise StageControlError("circuit_breaker_stage_invalid")
        if stage_envelope.scope != "stage":
            raise StageControlError("circuit_breaker_stage_scope_invalid")
        if not run_envelopes:
            raise StageControlError("circuit_breaker_run_envelopes_empty")
        if any(envelope.scope != "run" for envelope in run_envelopes.values()):
            raise StageControlError("circuit_breaker_run_scope_invalid")
        self._stage_id = stage_id
        self._stage_envelope = stage_envelope
        self._run_envelopes = dict(run_envelopes)
        self._record_sink = record_sink
        self._drain_policy = drain_policy
        self._lock = RLock()
        self._state = CircuitBreakerState.OPEN
        self._records: list[CircuitBreakerRecord] = []
        self._stage_consumed = {name: 0 for name in stage_envelope.limits}
        self._run_consumed = {
            run_id: {name: 0 for name in envelope.limits}
            for run_id, envelope in self._run_envelopes.items()
        }
        self._active: dict[AttemptId, CircuitBreakerAdmission] = {}
        self._terminal_evidence: dict[AttemptId, CircuitBreakerTerminalEvidence] = {}
        self._trip_reason: CircuitBreakerReason | None = None
        self._used_resume_authorization_ids: set[str] = set()
        if journal is None:
            self._append(CircuitBreakerState.OPEN, None, {})
        else:
            self._recover(journal)

    @property
    def state(self) -> CircuitBreakerState:
        with self._lock:
            return self._state

    @property
    def records(self) -> tuple[CircuitBreakerRecord, ...]:
        with self._lock:
            return tuple(self._records)

    @property
    def active_attempt_ids(self) -> tuple[AttemptId, ...]:
        with self._lock:
            return tuple(self._active)

    @property
    def terminal_evidence(self) -> tuple[CircuitBreakerTerminalEvidence, ...]:
        with self._lock:
            return tuple(self._terminal_evidence.values())

    def admit(
        self, attempt_id: AttemptId, run_id: RunId, requested: Mapping[str, int | float]
    ) -> CircuitBreakerAdmission:
        """Reserve both stage and run envelopes immediately before an attempt starts."""

        requested_quantities = _normalized_quantities(requested, "requested")
        with self._lock:
            return self._admit(attempt_id, run_id, requested_quantities)

    def admit_and_claim(
        self,
        attempt_id: AttemptId,
        run_id: RunId,
        requested: Mapping[str, int | float],
        claim: Callable[[], object],
    ) -> CircuitBreakerAdmission:
        """Reserve and issue the sole start claim within one breaker-owned lock."""

        requested_quantities = _normalized_quantities(requested, "requested")
        with self._lock:
            admission = self._admit(attempt_id, run_id, requested_quantities)
            try:
                claim()
            except AttemptExecutionClaimDeniedError:
                self.abort_unstarted(attempt_id)
                raise
            return admission

    def require_open(self) -> None:
        """Refuse a non-attempt admission path after any breaker trip."""

        with self._lock:
            if self._state is not CircuitBreakerState.OPEN:
                raise StageControlError("circuit_breaker_new_attempts_stopped", (self._state.value,))

    def observe(
        self,
        *,
        run_id: RunId,
        quantities: Mapping[str, int | float],
        reason: CircuitBreakerReason | None = None,
    ) -> None:
        """Append measured usage or an external integrity signal without fallback."""

        observed = _normalized_quantities(quantities, "observed")
        with self._lock:
            if run_id not in self._run_envelopes:
                raise StageControlError("circuit_breaker_run_envelope_missing")
            for name, value in observed.items():
                if name in self._stage_consumed and name not in self._run_consumed[run_id]:
                    self._stage_consumed[name] += value
                if name in self._run_consumed[run_id]:
                    prior = self._run_consumed[run_id][name]
                    if value > prior:
                        self._run_consumed[run_id][name] = value
                        if name in self._stage_consumed:
                            self._stage_consumed[name] += value - prior
            exceeded = self._at_or_over_limit(run_id)
            if reason is not None:
                self._trip(reason, observed)
            elif exceeded:
                self._trip(CircuitBreakerReason.LIMIT_EXCEEDED, observed)

    def trip(
        self, reason: CircuitBreakerReason, observed: Mapping[str, int | float] = {}
    ) -> None:
        """Stop all future admissions for a typed resource or integrity reason."""

        with self._lock:
            self._trip(reason, _normalized_quantities(observed, "observed"))

    def retain_terminal(self, evidence: CircuitBreakerTerminalEvidence) -> None:
        """Retain the one immutable terminal projection for an already-started attempt."""

        with self._lock:
            if evidence.attempt_id in self._terminal_evidence:
                raise StageControlError("circuit_breaker_terminal_already_retained")
            if evidence.attempt_id not in self._active:
                raise StageControlError("circuit_breaker_attempt_not_active")
            if (
                evidence.terminal_classification == "cancelled_by_circuit_breaker"
                and (
                    self._state is not CircuitBreakerState.DRAINING
                    or not self._drain_policy.allow_active_cancellation
                )
            ):
                raise StageControlError("circuit_breaker_cancellation_not_authorized")
            self._terminal_evidence[evidence.attempt_id] = evidence
            del self._active[evidence.attempt_id]
            if self._state is CircuitBreakerState.DRAINING and not self._active:
                self._state = CircuitBreakerState.CLOSED
                self._append(CircuitBreakerState.CLOSED, self._trip_reason, {})

    def may_cancel_active_attempt(self, attempt_id: AttemptId) -> bool:
        """Return whether the frozen drain policy permits cancelling this active attempt."""

        with self._lock:
            return bool(
                self._state is CircuitBreakerState.DRAINING
                and attempt_id in self._active
                and self._drain_policy.allow_active_cancellation
            )

    def abort_unstarted(self, attempt_id: AttemptId) -> None:
        """Remove a reservation only when no start receipt was ever issued.

        Reservations deliberately remain consumed. This only fixes the active
        bookkeeping when a later control-owned claim refuses an unstarted attempt.
        """

        with self._lock:
            if attempt_id not in self._active:
                raise StageControlError("circuit_breaker_attempt_not_active")
            del self._active[attempt_id]
            if self._state is CircuitBreakerState.DRAINING and not self._active:
                self._state = CircuitBreakerState.CLOSED
                self._append(CircuitBreakerState.CLOSED, self._trip_reason, {})

    def resume(
        self,
        *,
        authorization: CircuitBreakerResumeAuthorization,
        locks_unchanged: bool,
        repair_evidence_verified: bool,
        reconciliation_healthy: bool,
        backup_healthy: bool,
        now: datetime | None = None,
    ) -> None:
        """Reopen only after independent repair authorization and retained evidence checks."""

        with self._lock:
            if self._state is not CircuitBreakerState.CLOSED:
                raise StageControlError("circuit_breaker_resume_not_closed")
            if (
                authorization.stage_id != self._stage_id
                or authorization.reason is not self._trip_reason
                or authorization.stage_source_sha256 != self._stage_envelope.source_sha256
                or authorization.authorization_id in self._used_resume_authorization_ids
                or not authorization.is_current(now or datetime.now(UTC))
            ):
                raise StageControlError("circuit_breaker_resume_authorization_invalid")
            if not locks_unchanged:
                raise StageControlError("circuit_breaker_resume_lock_drift")
            if not repair_evidence_verified:
                raise StageControlError("circuit_breaker_resume_repair_evidence_missing")
            if not reconciliation_healthy:
                raise StageControlError("circuit_breaker_resume_reconciliation_unhealthy")
            if not backup_healthy:
                raise StageControlError("circuit_breaker_resume_backup_unhealthy")
            if any(self._at_or_over_limit(run_id) for run_id in self._run_envelopes):
                raise StageControlError("circuit_breaker_resume_limits_exhausted")
            self._used_resume_authorization_ids.add(authorization.authorization_id)
            self._state = CircuitBreakerState.OPEN
            self._append(
                CircuitBreakerState.OPEN,
                self._trip_reason,
                {},
                authorization.authorization_id,
            )

    def status_projection(self) -> dict[str, object]:
        """Expose only outcome-blind consumption, state, and active/draining counts."""

        with self._lock:
            return {
                "state": self._state.value,
                "reason": self._trip_reason.value if self._trip_reason is not None else None,
                "active_attempts": len(self._active),
                "draining_attempts": len(self._active)
                if self._state is CircuitBreakerState.DRAINING
                else 0,
                "stage": self._headroom(self._stage_envelope, self._stage_consumed),
                "runs": {
                    str(run_id): self._headroom(self._run_envelopes[run_id], consumed)
                    for run_id, consumed in self._run_consumed.items()
                },
            }

    def _would_exceed(
        self,
        requested: Mapping[str, int | float],
        stage_consumed: Mapping[str, int | float],
        stage_envelope: FrozenLimitEnvelope,
        run_consumed: Mapping[str, int | float],
        run_envelope: FrozenLimitEnvelope,
    ) -> tuple[str, ...]:
        exceeded: list[str] = []
        for name, value in requested.items():
            if name in stage_envelope.limits and stage_consumed[name] + value > stage_envelope.limits[name]:
                exceeded.append(f"stage:{name}")
            if name in run_envelope.limits and run_consumed[name] + value > run_envelope.limits[name]:
                exceeded.append(f"run:{name}")
        return tuple(exceeded)

    def _admit(
        self,
        attempt_id: AttemptId,
        run_id: RunId,
        requested_quantities: Mapping[str, int | float],
    ) -> CircuitBreakerAdmission:
        if self._state is not CircuitBreakerState.OPEN:
            raise StageControlError("circuit_breaker_new_attempts_stopped", (self._state.value,))
        if attempt_id in self._active or attempt_id in self._terminal_evidence:
            raise StageControlError("circuit_breaker_attempt_already_accounted")
        run_envelope = self._run_envelopes.get(run_id)
        if run_envelope is None:
            raise StageControlError("circuit_breaker_run_envelope_missing")
        exceeded = self._would_exceed(
            requested_quantities,
            self._stage_consumed,
            self._stage_envelope,
            self._run_consumed[run_id],
            run_envelope,
        )
        if exceeded:
            self._trip(CircuitBreakerReason.LIMIT_EXCEEDED, requested_quantities)
            raise StageControlError("circuit_breaker_limit_exceeded", tuple(exceeded))
        for name, value in requested_quantities.items():
            if name in self._stage_consumed:
                self._stage_consumed[name] += value
            if name in self._run_consumed[run_id]:
                self._run_consumed[run_id][name] += value
        admission = CircuitBreakerAdmission(
            attempt_id=attempt_id,
            run_id=run_id,
            stage_id=self._stage_id,
            reserved=requested_quantities,
            stage_source_sha256=self._stage_envelope.source_sha256,
            run_source_sha256=run_envelope.source_sha256,
        )
        self._active[attempt_id] = admission
        # The receipt that reaches an exact cap remains part of ITT. It also
        # closes admission before any later worker can claim zero or alternate
        # provider headroom.
        if self._at_or_over_limit(run_id):
            self._trip(CircuitBreakerReason.LIMIT_EXCEEDED, requested_quantities)
        return admission

    def _at_or_over_limit(self, run_id: RunId) -> bool:
        return any(
            self._stage_consumed[name] >= limit for name, limit in self._stage_envelope.limits.items()
        ) or any(
            self._run_consumed[run_id][name] >= limit
            for name, limit in self._run_envelopes[run_id].limits.items()
        )

    def _trip(
        self, reason: CircuitBreakerReason, observed: Mapping[str, int | float]
    ) -> None:
        if self._state is CircuitBreakerState.OPEN:
            self._trip_reason = reason
            self._state = CircuitBreakerState.TRIPPED
            self._append(CircuitBreakerState.TRIPPED, reason, observed)
            if self._active:
                self._state = CircuitBreakerState.DRAINING
                self._append(CircuitBreakerState.DRAINING, reason, observed)
            else:
                self._state = CircuitBreakerState.CLOSED
                self._append(CircuitBreakerState.CLOSED, reason, observed)
            return
        if self._trip_reason is reason:
            return
        self._append(self._state, reason, observed)

    def _append(
        self,
        state: CircuitBreakerState,
        reason: CircuitBreakerReason | None,
        observed: Mapping[str, int | float],
        resume_authorization_id: str | None = None,
    ) -> None:
        record = CircuitBreakerRecord(
            stage_id=self._stage_id,
            state=state,
            reason=reason,
            source_hashes={
                "stage": self._stage_envelope.source_sha256,
                **{
                    f"run:{run_id}": envelope.source_sha256
                    for run_id, envelope in self._run_envelopes.items()
                },
            },
            observed=observed,
            sequence=len(self._records),
            resume_authorization_id=resume_authorization_id,
        )
        self._records.append(record)
        if self._record_sink is not None:
            self._record_sink(record)

    def _recover(self, journal: tuple[CircuitBreakerRecord, ...]) -> None:
        """Restore a journal only into a state that cannot admit unreconciled work."""

        if not journal:
            raise StageControlError("circuit_breaker_journal_missing")
        expected_hashes = {
            "stage": self._stage_envelope.source_sha256,
            **{
                f"run:{run_id}": envelope.source_sha256
                for run_id, envelope in self._run_envelopes.items()
            },
        }
        for sequence, record in enumerate(journal):
            if (
                record.stage_id != self._stage_id
                or record.sequence != sequence
                or dict(record.source_hashes) != expected_hashes
            ):
                raise StageControlError("circuit_breaker_journal_invalid")
        self._records = list(journal)
        self._used_resume_authorization_ids = {
            record.resume_authorization_id
            for record in journal
            if record.resume_authorization_id is not None
        }
        final = journal[-1]
        self._trip_reason = final.reason
        # Runtime reservations and active worker handles are deliberately not
        # reconstructed from a process restart. Keep admission closed until repair
        # and reconciliation establish a new authorization.
        self._state = CircuitBreakerState.CLOSED
        if final.state is CircuitBreakerState.OPEN:
            self._trip_reason = CircuitBreakerReason.LOCK_DRIFT
            self._append(CircuitBreakerState.CLOSED, self._trip_reason, {})

    @staticmethod
    def _headroom(
        envelope: FrozenLimitEnvelope, consumed: Mapping[str, int | float]
    ) -> dict[str, object]:
        return {
            name: {
                "limit": limit,
                "consumed": consumed[name],
                "remaining": max(0, limit - consumed[name]),
                "exhausted": consumed[name] >= limit,
            }
            for name, limit in envelope.limits.items()
        }


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
            or not math.isfinite(cap)
            or not math.isfinite(used)
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
