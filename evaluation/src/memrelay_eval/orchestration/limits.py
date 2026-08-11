"""Bounded local concurrency for disposable attempt workers."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock, RLock
from types import MappingProxyType
from typing import Final, cast
from uuid import uuid4

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
    try:
        finite = isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if isinstance(value, bool) or not finite or value < 0:
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
        object.__setattr__(
            self, "source_sha256", _require_sha256(self.source_sha256, "source_sha256")
        )
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
    snapshot: Mapping[str, object] = field(default_factory=dict)

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
        if not isinstance(self.snapshot, Mapping):
            raise StageControlError("circuit_breaker_snapshot_invalid")
        source_hashes = {
            key: _require_sha256(value, f"source_hashes.{key}")
            for key, value in self.source_hashes.items()
        }
        if not source_hashes:
            raise StageControlError("circuit_breaker_source_hashes_empty")
        object.__setattr__(
            self, "source_hashes", MappingProxyType(dict(sorted(source_hashes.items())))
        )
        object.__setattr__(self, "observed", _normalized_quantities(self.observed, "observed"))
        object.__setattr__(self, "snapshot", MappingProxyType(dict(self.snapshot)))

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
            "snapshot": dict(self.snapshot),
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def bytes(self) -> bytes:
        return canonical_bytes({**self.to_document(), "digest": self.digest})


def load_circuit_breaker_record(data: bytes) -> CircuitBreakerRecord:
    """Load only an exact canonical, digest-bound circuit-breaker record."""

    try:
        document = json.loads(data)
    except (TypeError, json.JSONDecodeError) as error:
        raise StageControlError("circuit_breaker_journal_invalid") from error
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "artifact_type",
        "stage_id",
        "state",
        "reason",
        "source_hashes",
        "observed",
        "at",
        "sequence",
        "resume_authorization_id",
        "snapshot",
        "digest",
    }:
        raise StageControlError("circuit_breaker_journal_invalid")
    digest = document.pop("digest")
    if (
        document["schema_version"] != "1.0.0"
        or document["artifact_type"] != "circuit_breaker_record"
        or not isinstance(digest, str)
        or digest != canonical_digest(document)
        or data != canonical_bytes({**document, "digest": digest})
    ):
        raise StageControlError("circuit_breaker_journal_invalid")
    try:
        timestamp = datetime.fromisoformat(str(document["at"]).replace("Z", "+00:00"))
        reason = document["reason"]
        return CircuitBreakerRecord(
            stage_id=cast(str, document["stage_id"]),
            state=CircuitBreakerState(document["state"]),
            reason=None if reason is None else CircuitBreakerReason(reason),
            source_hashes=cast(Mapping[str, str], document["source_hashes"]),
            observed=cast(Mapping[str, int | float], document["observed"]),
            at=timestamp,
            sequence=cast(int, document["sequence"]),
            resume_authorization_id=cast(str | None, document["resume_authorization_id"]),
            snapshot=cast(Mapping[str, object], document["snapshot"]),
        )
    except (TypeError, ValueError, StageControlError) as error:
        raise StageControlError("circuit_breaker_journal_invalid") from error


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
        drain_policy: CircuitBreakerDrainPolicy | None = None,
        journal: tuple[CircuitBreakerRecord | bytes, ...] | None = None,
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
        self._drain_policy = drain_policy or CircuitBreakerDrainPolicy()
        self._lock = RLock()
        self._state = CircuitBreakerState.OPEN
        self._records: list[CircuitBreakerRecord] = []
        self._stage_consumed = dict.fromkeys(stage_envelope.limits, 0)
        self._run_consumed = {
            run_id: dict.fromkeys(envelope.limits, 0)
            for run_id, envelope in self._run_envelopes.items()
        }
        self._active: dict[AttemptId, CircuitBreakerAdmission] = {}
        self._claimed_active: set[AttemptId] = set()
        self._external_start_claims: set[str] = set()
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
            self._claimed_active.add(attempt_id)
            self._append(self._state, self._trip_reason, {})
            return admission

    def require_open(self) -> None:
        """Refuse a non-attempt admission path after any breaker trip."""

        with self._lock:
            if self._state is not CircuitBreakerState.OPEN:
                raise StageControlError(
                    "circuit_breaker_new_attempts_stopped", (self._state.value,)
                )

    def start_external_operation(self, operation: Callable[[], object]) -> object:
        """Consume an atomic start claim immediately before an external side effect."""

        claim_id = self.claim_external_start()
        try:
            return operation()
        finally:
            self.complete_external_start(claim_id)

    def claim_external_start(self) -> str:
        """Issue one durable start claim while the breaker is still open."""

        with self._lock:
            if self._state is not CircuitBreakerState.OPEN:
                raise StageControlError(
                    "circuit_breaker_new_attempts_stopped", (self._state.value,)
                )
            claim_id = str(uuid4())
            self._external_start_claims.add(claim_id)
            self._append(CircuitBreakerState.OPEN, self._trip_reason, {})
            return claim_id

    def complete_external_start(self, claim_id: str) -> None:
        """Retire exactly one completed external start claim without reopening admission."""

        with self._lock:
            if claim_id not in self._external_start_claims:
                raise StageControlError("circuit_breaker_external_claim_invalid")
            self._external_start_claims.remove(claim_id)
            if (
                self._state is CircuitBreakerState.DRAINING
                and not self._active
                and not self._external_start_claims
            ):
                self._state = CircuitBreakerState.CLOSED
                self._append(CircuitBreakerState.CLOSED, self._trip_reason, {})
            else:
                self._append(self._state, self._trip_reason, {})

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
            else:
                self._append(self._state, self._trip_reason, observed)

    def trip(self, reason: CircuitBreakerReason, observed: Mapping[str, int | float] = {}) -> None:
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
            if evidence.terminal_classification == "cancelled_by_circuit_breaker" and (
                self._state is not CircuitBreakerState.DRAINING
                or not self._drain_policy.allow_active_cancellation
            ):
                raise StageControlError("circuit_breaker_cancellation_not_authorized")
            self._terminal_evidence[evidence.attempt_id] = evidence
            del self._active[evidence.attempt_id]
            self._claimed_active.discard(evidence.attempt_id)
            if (
                self._state is CircuitBreakerState.DRAINING
                and not self._active
                and not self._external_start_claims
            ):
                self._state = CircuitBreakerState.CLOSED
                self._append(CircuitBreakerState.CLOSED, self._trip_reason, {})
            else:
                self._append(self._state, self._trip_reason, {})

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
            self._claimed_active.discard(attempt_id)
            if (
                self._state is CircuitBreakerState.DRAINING
                and not self._active
                and not self._external_start_claims
            ):
                self._state = CircuitBreakerState.CLOSED
                self._append(CircuitBreakerState.CLOSED, self._trip_reason, {})
            else:
                self._append(self._state, self._trip_reason, {})

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
                "active_external_operations": len(self._external_start_claims),
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
            if (
                name in stage_envelope.limits
                and stage_consumed[name] + value > stage_envelope.limits[name]
            ):
                exceeded.append(f"stage:{name}")
            if (
                name in run_envelope.limits
                and run_consumed[name] + value > run_envelope.limits[name]
            ):
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
        else:
            self._append(CircuitBreakerState.OPEN, None, requested_quantities)
        return admission

    def _at_or_over_limit(self, run_id: RunId) -> bool:
        return any(
            self._stage_consumed[name] >= limit
            for name, limit in self._stage_envelope.limits.items()
        ) or any(
            self._run_consumed[run_id][name] >= limit
            for name, limit in self._run_envelopes[run_id].limits.items()
        )

    def _trip(self, reason: CircuitBreakerReason, observed: Mapping[str, int | float]) -> None:
        if self._state is CircuitBreakerState.OPEN:
            self._trip_reason = reason
            self._state = CircuitBreakerState.TRIPPED
            self._append(CircuitBreakerState.TRIPPED, reason, observed)
            if self._active or self._external_start_claims:
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
            snapshot=self._snapshot(),
        )
        self._records.append(record)
        if self._record_sink is not None:
            self._record_sink(record)

    def _snapshot(self) -> dict[str, object]:
        return {
            "trip_reason": self._trip_reason.value if self._trip_reason is not None else None,
            "stage_consumed": dict(self._stage_consumed),
            "run_consumed": {
                str(run_id): dict(consumed) for run_id, consumed in self._run_consumed.items()
            },
            "active": {
                str(attempt_id): {
                    "run_id": str(admission.run_id),
                    "reserved": dict(admission.reserved),
                    "claimed": attempt_id in self._claimed_active,
                }
                for attempt_id, admission in self._active.items()
            },
            "external_start_claims": sorted(self._external_start_claims),
            "terminal_evidence": {
                str(attempt_id): {
                    "terminal_classification": evidence.terminal_classification,
                    "exposure_state": evidence.exposure_state,
                    "cost_quantities": dict(evidence.cost_quantities),
                    "evidence_refs": list(evidence.evidence_refs),
                    "itt_retained": evidence.itt_retained,
                }
                for attempt_id, evidence in self._terminal_evidence.items()
            },
            "used_resume_authorization_ids": sorted(self._used_resume_authorization_ids),
        }

    def _recover(self, journal: tuple[CircuitBreakerRecord | bytes, ...]) -> None:
        """Replay immutable snapshots without ever defaulting missing usage to zero."""

        if not journal:
            raise StageControlError("circuit_breaker_journal_missing")
        records = tuple(
            record
            if isinstance(record, CircuitBreakerRecord)
            else load_circuit_breaker_record(record)
            for record in journal
        )
        expected_hashes = {
            "stage": self._stage_envelope.source_sha256,
            **{
                f"run:{run_id}": envelope.source_sha256
                for run_id, envelope in self._run_envelopes.items()
            },
        }
        previous: CircuitBreakerRecord | None = None
        previous_snapshot: dict[str, object] | None = None
        for sequence, record in enumerate(records):
            if (
                record.stage_id != self._stage_id
                or record.sequence != sequence
                or dict(record.source_hashes) != expected_hashes
            ):
                raise StageControlError("circuit_breaker_journal_invalid")
            if previous is None:
                if record.state is not CircuitBreakerState.OPEN or record.reason is not None:
                    raise StageControlError("circuit_breaker_journal_invalid")
            elif not self._valid_transition(previous, record):
                raise StageControlError("circuit_breaker_journal_invalid")
            try:
                snapshot = self._validated_snapshot(record.snapshot, record.state)
            except StageControlError as error:
                raise StageControlError("circuit_breaker_journal_invalid") from error
            if previous_snapshot is None:
                self._validate_initial_snapshot(snapshot)
            else:
                self._validate_snapshot_progression(
                    previous_snapshot,
                    snapshot,
                    record,
                )
            previous_snapshot = snapshot
            previous = record

        final = records[-1]
        if previous_snapshot is None:
            raise StageControlError("circuit_breaker_journal_invalid")
        self._records = list(records)
        self._stage_consumed = previous_snapshot["stage_consumed"]
        self._run_consumed = previous_snapshot["run_consumed"]
        self._active = previous_snapshot["active"]
        self._claimed_active = previous_snapshot["claimed_active"]
        self._external_start_claims = previous_snapshot["external_start_claims"]
        self._terminal_evidence = previous_snapshot["terminal_evidence"]
        self._used_resume_authorization_ids = previous_snapshot["used_resume_authorization_ids"]
        self._trip_reason = previous_snapshot["trip_reason"]
        self._state = final.state

    @staticmethod
    def _valid_transition(previous: CircuitBreakerRecord, current: CircuitBreakerRecord) -> bool:
        allowed = {
            CircuitBreakerState.OPEN: {
                CircuitBreakerState.OPEN,
                CircuitBreakerState.TRIPPED,
            },
            CircuitBreakerState.TRIPPED: {
                CircuitBreakerState.DRAINING,
                CircuitBreakerState.CLOSED,
            },
            CircuitBreakerState.DRAINING: {
                CircuitBreakerState.DRAINING,
                CircuitBreakerState.CLOSED,
            },
            CircuitBreakerState.CLOSED: {
                CircuitBreakerState.CLOSED,
                CircuitBreakerState.OPEN,
            },
        }
        if current.state not in allowed[previous.state]:
            return False
        if (
            current.state is CircuitBreakerState.OPEN
            and previous.state is CircuitBreakerState.CLOSED
        ):
            return current.resume_authorization_id is not None
        return current.resume_authorization_id is None

    def _validated_snapshot(
        self, value: Mapping[str, object], state: CircuitBreakerState
    ) -> dict[str, object]:
        required = {
            "trip_reason",
            "stage_consumed",
            "run_consumed",
            "active",
            "external_start_claims",
            "terminal_evidence",
            "used_resume_authorization_ids",
        }
        if set(value) != required:
            raise StageControlError("circuit_breaker_journal_invalid")
        trip_reason_value = value["trip_reason"]
        if trip_reason_value is None:
            trip_reason = None
        elif isinstance(trip_reason_value, str):
            try:
                trip_reason = CircuitBreakerReason(trip_reason_value)
            except ValueError as error:
                raise StageControlError("circuit_breaker_journal_invalid") from error
        else:
            raise StageControlError("circuit_breaker_journal_invalid")
        if state is CircuitBreakerState.OPEN and trip_reason is not None:
            # A post-repair open breaker retains its prior trip reason for its
            # authorization scope, so it is the sole exception to this shape.
            pass
        elif state is CircuitBreakerState.OPEN:
            trip_reason = None
        elif trip_reason is None:
            raise StageControlError("circuit_breaker_journal_invalid")

        stage_consumed = self._snapshot_quantities(
            value["stage_consumed"], self._stage_envelope.limits
        )
        run_consumed_document = self._mapping(value["run_consumed"])
        if set(run_consumed_document) != {str(run_id) for run_id in self._run_envelopes}:
            raise StageControlError("circuit_breaker_journal_invalid")
        run_consumed: dict[RunId, dict[str, int | float]] = {}
        for run_id, envelope in self._run_envelopes.items():
            run_consumed[run_id] = self._snapshot_quantities(
                run_consumed_document[str(run_id)], envelope.limits
            )

        active_document = self._mapping(value["active"])
        external_claims = value["external_start_claims"]
        if (
            not isinstance(external_claims, list)
            or not all(isinstance(claim_id, str) and claim_id for claim_id in external_claims)
            or len(set(external_claims)) != len(external_claims)
        ):
            raise StageControlError("circuit_breaker_journal_invalid")
        terminal_document = self._mapping(value["terminal_evidence"])
        if set(active_document) & set(terminal_document):
            raise StageControlError("circuit_breaker_journal_invalid")
        active: dict[AttemptId, CircuitBreakerAdmission] = {}
        claimed_active: set[AttemptId] = set()
        for attempt_text, raw in active_document.items():
            attempt_id = self._attempt_id(attempt_text)
            document = self._mapping(raw)
            if set(document) != {"run_id", "reserved", "claimed"}:
                raise StageControlError("circuit_breaker_journal_invalid")
            run_id = self._run_id(document["run_id"])
            envelope = self._run_envelopes.get(run_id)
            if envelope is None or not isinstance(document["claimed"], bool):
                raise StageControlError("circuit_breaker_journal_invalid")
            reserved = _normalized_quantities(
                cast(Mapping[str, int | float], self._mapping(document["reserved"])),
                "reserved",
            )
            if any(
                quantity > run_consumed[run_id].get(name, 0) for name, quantity in reserved.items()
            ):
                raise StageControlError("circuit_breaker_journal_invalid")
            active[attempt_id] = CircuitBreakerAdmission(
                attempt_id=attempt_id,
                run_id=run_id,
                stage_id=self._stage_id,
                reserved=reserved,
                stage_source_sha256=self._stage_envelope.source_sha256,
                run_source_sha256=envelope.source_sha256,
            )
            if document["claimed"]:
                claimed_active.add(attempt_id)

        terminal_evidence: dict[AttemptId, CircuitBreakerTerminalEvidence] = {}
        for attempt_text, raw in terminal_document.items():
            attempt_id = self._attempt_id(attempt_text)
            document = self._mapping(raw)
            if set(document) != {
                "terminal_classification",
                "exposure_state",
                "cost_quantities",
                "evidence_refs",
                "itt_retained",
            }:
                raise StageControlError("circuit_breaker_journal_invalid")
            refs = document["evidence_refs"]
            if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
                raise StageControlError("circuit_breaker_journal_invalid")
            terminal_evidence[attempt_id] = CircuitBreakerTerminalEvidence(
                attempt_id=attempt_id,
                terminal_classification=self._string(document["terminal_classification"]),
                exposure_state=self._string(document["exposure_state"]),
                cost_quantities=_normalized_quantities(
                    cast(Mapping[str, int | float], self._mapping(document["cost_quantities"])),
                    "cost_quantities",
                ),
                evidence_refs=tuple(refs),
                itt_retained=document["itt_retained"] is True,
            )

        used = value["used_resume_authorization_ids"]
        if (
            not isinstance(used, list)
            or not all(isinstance(identifier, str) and identifier for identifier in used)
            or len(set(used)) != len(used)
        ):
            raise StageControlError("circuit_breaker_journal_invalid")
        if state is CircuitBreakerState.OPEN and (
            any(
                stage_consumed[name] >= limit for name, limit in self._stage_envelope.limits.items()
            )
            or any(
                run_consumed[run_id][name] >= limit
                for run_id, envelope in self._run_envelopes.items()
                for name, limit in envelope.limits.items()
            )
        ):
            raise StageControlError("circuit_breaker_journal_invalid")
        if state is CircuitBreakerState.CLOSED and (active or external_claims):
            raise StageControlError("circuit_breaker_journal_invalid")
        return {
            "trip_reason": trip_reason,
            "stage_consumed": stage_consumed,
            "run_consumed": run_consumed,
            "active": active,
            "claimed_active": claimed_active,
            "external_start_claims": set(external_claims),
            "terminal_evidence": terminal_evidence,
            "used_resume_authorization_ids": set(used),
        }

    @staticmethod
    def _validate_initial_snapshot(snapshot: Mapping[str, object]) -> None:
        stage_consumed = cast(Mapping[str, int | float], snapshot["stage_consumed"])
        run_consumed = cast(
            Mapping[RunId, Mapping[str, int | float]],
            snapshot["run_consumed"],
        )
        if (
            snapshot["trip_reason"] is not None
            or any(stage_consumed.values())
            or any(quantity for consumed in run_consumed.values() for quantity in consumed.values())
            or snapshot["active"]
            or snapshot["claimed_active"]
            or snapshot["external_start_claims"]
            or snapshot["terminal_evidence"]
            or snapshot["used_resume_authorization_ids"]
        ):
            raise StageControlError("circuit_breaker_journal_invalid")

    @staticmethod
    def _validate_snapshot_progression(
        previous: Mapping[str, object],
        current: Mapping[str, object],
        current_record: CircuitBreakerRecord,
    ) -> None:
        previous_stage = cast(Mapping[str, int | float], previous["stage_consumed"])
        current_stage = cast(Mapping[str, int | float], current["stage_consumed"])
        previous_runs = cast(
            Mapping[RunId, Mapping[str, int | float]],
            previous["run_consumed"],
        )
        current_runs = cast(
            Mapping[RunId, Mapping[str, int | float]],
            current["run_consumed"],
        )
        previous_terminal = cast(
            Mapping[AttemptId, CircuitBreakerTerminalEvidence],
            previous["terminal_evidence"],
        )
        current_terminal = cast(
            Mapping[AttemptId, CircuitBreakerTerminalEvidence],
            current["terminal_evidence"],
        )
        previous_active = cast(Mapping[AttemptId, CircuitBreakerAdmission], previous["active"])
        current_active = cast(Mapping[AttemptId, CircuitBreakerAdmission], current["active"])
        previous_claimed = cast(set[AttemptId], previous["claimed_active"])
        current_claimed = cast(set[AttemptId], current["claimed_active"])
        if (
            any(current_stage[name] < quantity for name, quantity in previous_stage.items())
            or any(
                current_runs[run_id][name] < quantity
                for run_id, consumed in previous_runs.items()
                for name, quantity in consumed.items()
            )
            or not previous["used_resume_authorization_ids"]
            <= current["used_resume_authorization_ids"]
            or any(
                current_terminal.get(attempt_id) != evidence
                for attempt_id, evidence in previous_terminal.items()
            )
        ):
            raise StageControlError("circuit_breaker_journal_invalid")
        transitioned = set(previous_active) - set(current_active)
        terminalized = set(current_terminal) - set(previous_terminal)
        abandoned_unclaimed = transitioned - terminalized
        if (
            terminalized - transitioned
            or any(attempt_id in previous_claimed for attempt_id in abandoned_unclaimed)
            or any(
                previous_active[attempt_id] != current_active[attempt_id]
                for attempt_id in set(previous_active) & set(current_active)
            )
            or not current_claimed <= set(current_active)
            or not (previous_claimed - transitioned) <= current_claimed
        ):
            raise StageControlError("circuit_breaker_journal_invalid")
        if current_record.state is CircuitBreakerState.OPEN and (
            current_record.resume_authorization_id is not None
            and current["trip_reason"] != previous["trip_reason"]
        ):
            raise StageControlError("circuit_breaker_journal_invalid")
        if current_record.state is not CircuitBreakerState.OPEN and current["trip_reason"] is None:
            raise StageControlError("circuit_breaker_journal_invalid")

    @staticmethod
    def _mapping(value: object) -> Mapping[str, object]:
        if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
            raise StageControlError("circuit_breaker_journal_invalid")
        return value

    @staticmethod
    def _string(value: object) -> str:
        if not isinstance(value, str) or not value:
            raise StageControlError("circuit_breaker_journal_invalid")
        return value

    def _snapshot_quantities(
        self, value: object, limits: Mapping[str, int | float]
    ) -> dict[str, int | float]:
        document = self._mapping(value)
        if set(document) != set(limits):
            raise StageControlError("circuit_breaker_journal_invalid")
        quantities = _normalized_quantities(cast(Mapping[str, int | float], document), "snapshot")
        if any(quantities[name] < 0 for name in limits):
            raise StageControlError("circuit_breaker_journal_invalid")
        return dict(quantities)

    @staticmethod
    def _attempt_id(value: object) -> AttemptId:
        try:
            return AttemptId(CircuitBreakerAdmissionController._string(value))
        except ValueError as error:
            raise StageControlError("circuit_breaker_journal_invalid") from error

    @staticmethod
    def _run_id(value: object) -> RunId:
        try:
            return RunId(CircuitBreakerAdmissionController._string(value))
        except ValueError as error:
            raise StageControlError("circuit_breaker_journal_invalid") from error

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
