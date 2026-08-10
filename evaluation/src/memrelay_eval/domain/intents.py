"""Immutable, thin worker-to-control ledger transition intents."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from typing import ClassVar

from memrelay_eval.canonical import canonical_bytes

from .entities import ArtifactLink, ArtifactRef, InclusionDecision
from .ids import AssignmentId, AttemptId, ExperimentId, IntentId, ProtocolId, RunId
from .states import AttemptTerminalKind, LedgerIntentKind, RunState

_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_METADATA_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_MAX_EVIDENCE_REFS = 16
_MAX_SAFE_METADATA = 16
_PROHIBITED_METADATA_TERMS = frozenset(
    {
        "arm",
        "body",
        "code",
        "condition",
        "credential",
        "event",
        "grader",
        "inspect",
        "patch",
        "password",
        "payload",
        "prompt",
        "provider",
        "repo",
        "repository",
        "secret",
        "token",
        "trace",
        "treatment",
    }
)


def _timestamp_payload(value: datetime) -> str:
    """Serialize a timestamp without silently repairing an invalid timezone."""

    if value.tzinfo is None or value.utcoffset() is None:
        return f"invalid-naive:{value.isoformat(timespec='microseconds')}"
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _artifact_payload(value: ArtifactRef) -> dict[str, object]:
    return {
        "artifact_id": str(value.artifact_id),
        "sha256": value.sha256,
        "size_bytes": value.size_bytes,
    }


@dataclass(frozen=True, slots=True)
class IntentMetadata:
    """Common delivery, causality, and thin-evidence information for an intent."""

    intent_id: IntentId
    occurred_at: datetime
    source_attempt_id: AttemptId | None = None
    expected_prior_state: RunState | None = None
    expected_prior_digest: str | None = None
    monotonic_ns: int | None = None
    evidence_refs: tuple[ArtifactRef, ...] = ()
    reason_code: str = "unspecified"
    safe_metadata: Mapping[str, bool | int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "safe_metadata", MappingProxyType(dict(self.safe_metadata)))

    def to_payload(self) -> dict[str, object]:
        return {
            "intent_id": str(self.intent_id),
            "occurred_at": _timestamp_payload(self.occurred_at),
            "source_attempt_id": (
                str(self.source_attempt_id) if self.source_attempt_id is not None else None
            ),
            "expected_prior_state": (
                self.expected_prior_state.value
                if isinstance(self.expected_prior_state, RunState)
                else self.expected_prior_state
            ),
            "expected_prior_digest": self.expected_prior_digest,
            "monotonic_ns": self.monotonic_ns,
            "evidence_refs": [_artifact_payload(item) for item in self.evidence_refs],
            "reason_code": self.reason_code,
            "safe_metadata": dict(self.safe_metadata),
        }

    def has_only_small_scalars(self) -> bool:
        return all(
            isinstance(value, bool)
            or (isinstance(value, int) and not isinstance(value, bool))
            or (isinstance(value, float) and isfinite(value))
            for value in self.safe_metadata.values()
        )


class LedgerIntent:
    """Shared immutable behavior for concrete, typed intent variants."""

    kind: ClassVar[LedgerIntentKind]
    metadata: IntentMetadata

    @property
    def intent_id(self) -> IntentId:
        return self.metadata.intent_id

    @property
    def canonical_payload_digest(self) -> str:
        return sha256(canonical_bytes(self.to_payload())).hexdigest()

    def to_payload(self) -> dict[str, object]:
        payload = self.metadata.to_payload()
        payload["kind"] = self.kind.value
        payload.update(self._operation_payload())
        return payload

    def _operation_payload(self) -> dict[str, object]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class CreateExperimentIntent(LedgerIntent):
    metadata: IntentMetadata
    experiment_id: ExperimentId
    protocol_id: ProtocolId

    kind: ClassVar[LedgerIntentKind] = LedgerIntentKind.CREATE_EXPERIMENT

    def _operation_payload(self) -> dict[str, object]:
        return {"experiment_id": str(self.experiment_id), "protocol_id": str(self.protocol_id)}


@dataclass(frozen=True, slots=True)
class CreateRunIntent(LedgerIntent):
    metadata: IntentMetadata
    run_id: RunId
    experiment_id: ExperimentId
    assignment_id: AssignmentId

    kind: ClassVar[LedgerIntentKind] = LedgerIntentKind.CREATE_RUN

    def _operation_payload(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "experiment_id": str(self.experiment_id),
            "assignment_id": str(self.assignment_id),
        }


@dataclass(frozen=True, slots=True)
class CreateAttemptIntent(LedgerIntent):
    metadata: IntentMetadata
    attempt_id: AttemptId
    run_id: RunId

    kind: ClassVar[LedgerIntentKind] = LedgerIntentKind.CREATE_ATTEMPT

    def _operation_payload(self) -> dict[str, object]:
        return {"attempt_id": str(self.attempt_id), "run_id": str(self.run_id)}


@dataclass(frozen=True, slots=True)
class RunTransitionIntent(LedgerIntent):
    metadata: IntentMetadata
    run_id: RunId
    previous: RunState
    next_state: RunState

    kind: ClassVar[LedgerIntentKind] = LedgerIntentKind.RUN_TRANSITION

    def _operation_payload(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "previous": self.previous.value
            if isinstance(self.previous, RunState)
            else self.previous,
            "next_state": (
                self.next_state.value if isinstance(self.next_state, RunState) else self.next_state
            ),
        }


@dataclass(frozen=True, slots=True)
class AttemptTerminalIntent(LedgerIntent):
    metadata: IntentMetadata
    attempt_id: AttemptId
    run_id: RunId
    classification: AttemptTerminalKind
    pre_exposure_evidence: ArtifactRef | None = None

    kind: ClassVar[LedgerIntentKind] = LedgerIntentKind.ATTEMPT_TERMINAL

    def _operation_payload(self) -> dict[str, object]:
        return {
            "attempt_id": str(self.attempt_id),
            "run_id": str(self.run_id),
            "classification": (
                self.classification.value
                if isinstance(self.classification, AttemptTerminalKind)
                else self.classification
            ),
            "pre_exposure_evidence": (
                _artifact_payload(self.pre_exposure_evidence)
                if self.pre_exposure_evidence is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class ArtifactLinkIntent(LedgerIntent):
    metadata: IntentMetadata
    link: ArtifactLink

    kind: ClassVar[LedgerIntentKind] = LedgerIntentKind.ARTIFACT_LINK

    def _operation_payload(self) -> dict[str, object]:
        return {
            "artifact": _artifact_payload(self.link.artifact_ref),
            "purpose": self.link.purpose,
            "experiment_id": str(self.link.experiment_id) if self.link.experiment_id else None,
            "run_id": str(self.link.run_id) if self.link.run_id else None,
            "attempt_id": str(self.link.attempt_id) if self.link.attempt_id else None,
        }


@dataclass(frozen=True, slots=True)
class RetryLineageIntent(LedgerIntent):
    metadata: IntentMetadata
    run_id: RunId
    previous_attempt_id: AttemptId
    retry_attempt_id: AttemptId

    kind: ClassVar[LedgerIntentKind] = LedgerIntentKind.RETRY_LINEAGE

    def _operation_payload(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "previous_attempt_id": str(self.previous_attempt_id),
            "retry_attempt_id": str(self.retry_attempt_id),
        }


@dataclass(frozen=True, slots=True)
class InclusionDecisionIntent(LedgerIntent):
    metadata: IntentMetadata
    decision: InclusionDecision

    kind: ClassVar[LedgerIntentKind] = LedgerIntentKind.INCLUSION_DECISION

    def _operation_payload(self) -> dict[str, object]:
        return {
            "inclusion_id": str(self.decision.id),
            "run_id": str(self.decision.run_id),
            "status": self.decision.status.value,
            "reason_code": self.decision.reason,
            "reconciliation_sha256": self.decision.reconciliation_sha256,
            "decision_occurred_at": _timestamp_payload(self.decision.occurred_at),
        }


@dataclass(frozen=True, slots=True)
class AuthorityConflictIntent(LedgerIntent):
    """Control-owned, append-only ineligibility fact with evidence references only."""

    metadata: IntentMetadata
    run_id: RunId
    attempt_id: AttemptId
    conflict_fields: tuple[str, ...]

    kind: ClassVar[LedgerIntentKind] = LedgerIntentKind.AUTHORITY_CONFLICT

    def _operation_payload(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "attempt_id": str(self.attempt_id),
            "conflict_fields": list(self.conflict_fields),
        }


LedgerIntentType = (
    CreateExperimentIntent
    | CreateRunIntent
    | CreateAttemptIntent
    | RunTransitionIntent
    | AttemptTerminalIntent
    | ArtifactLinkIntent
    | RetryLineageIntent
    | InclusionDecisionIntent
    | AuthorityConflictIntent
)


def preflight_intent_rejection(intent: object) -> str | None:
    """Return a stable rejection code before serializing any untrusted intent field."""

    metadata = getattr(intent, "metadata", None)
    if not isinstance(metadata, IntentMetadata) or not isinstance(metadata.intent_id, IntentId):
        return "invalid_intent_metadata"
    if not _is_utc_timestamp(metadata.occurred_at):
        return "non_utc_timestamp"
    if metadata.source_attempt_id is not None and not isinstance(
        metadata.source_attempt_id, AttemptId
    ):
        return "invalid_source_attempt"
    if metadata.expected_prior_state is not None and not isinstance(
        metadata.expected_prior_state, RunState
    ):
        return "invalid_expected_state"
    if metadata.expected_prior_digest is not None and (
        not isinstance(metadata.expected_prior_digest, str)
        or not _DIGEST.fullmatch(metadata.expected_prior_digest)
    ):
        return "invalid_expected_digest"
    if metadata.monotonic_ns is not None and (
        isinstance(metadata.monotonic_ns, bool)
        or not isinstance(metadata.monotonic_ns, int)
        or metadata.monotonic_ns < 0
    ):
        return "invalid_monotonic_time"
    if not isinstance(metadata.reason_code, str) or not _REASON_CODE.fullmatch(
        metadata.reason_code
    ):
        return "invalid_reason_code"
    if not isinstance(metadata.evidence_refs, tuple) or any(
        not isinstance(reference, ArtifactRef) for reference in metadata.evidence_refs
    ):
        return "invalid_evidence_refs"
    if len(metadata.evidence_refs) > _MAX_EVIDENCE_REFS:
        return "invalid_evidence_refs"
    if (
        not isinstance(metadata.safe_metadata, Mapping)
        or len(metadata.safe_metadata) > _MAX_SAFE_METADATA
        or not metadata.has_only_small_scalars()
    ):
        return "thin_ledger_violation"
    if any(
        not isinstance(key, str)
        or not _METADATA_KEY.fullmatch(key)
        or _contains_prohibited_metadata_term(key)
        for key in metadata.safe_metadata
    ):
        return "thin_ledger_violation"
    if isinstance(intent, CreateExperimentIntent):
        valid = isinstance(intent.experiment_id, ExperimentId) and isinstance(
            intent.protocol_id, ProtocolId
        )
    elif isinstance(intent, CreateRunIntent):
        valid = (
            isinstance(intent.run_id, RunId)
            and isinstance(intent.experiment_id, ExperimentId)
            and isinstance(intent.assignment_id, AssignmentId)
        )
    elif isinstance(intent, CreateAttemptIntent):
        valid = isinstance(intent.attempt_id, AttemptId) and isinstance(intent.run_id, RunId)
    elif isinstance(intent, RunTransitionIntent):
        valid = (
            isinstance(intent.run_id, RunId)
            and isinstance(intent.previous, RunState)
            and isinstance(intent.next_state, RunState)
        )
    elif isinstance(intent, AttemptTerminalIntent):
        valid = (
            isinstance(intent.attempt_id, AttemptId)
            and isinstance(intent.run_id, RunId)
            and isinstance(intent.classification, AttemptTerminalKind)
            and (
                intent.pre_exposure_evidence is None
                or isinstance(intent.pre_exposure_evidence, ArtifactRef)
            )
        )
    elif isinstance(intent, ArtifactLinkIntent):
        valid = isinstance(intent.link, ArtifactLink) and isinstance(
            intent.link.artifact_ref, ArtifactRef
        )
    elif isinstance(intent, RetryLineageIntent):
        valid = (
            isinstance(intent.run_id, RunId)
            and isinstance(intent.previous_attempt_id, AttemptId)
            and isinstance(intent.retry_attempt_id, AttemptId)
        )
    elif isinstance(intent, InclusionDecisionIntent):
        valid = isinstance(intent.decision, InclusionDecision)
    elif isinstance(intent, AuthorityConflictIntent):
        valid = (
            isinstance(intent.run_id, RunId)
            and isinstance(intent.attempt_id, AttemptId)
            and bool(intent.conflict_fields)
            and all(
                isinstance(field, str) and _REASON_CODE.fullmatch(field)
                for field in intent.conflict_fields
            )
        )
    else:
        valid = False
    return None if valid else "invalid_opaque_identity"


def _is_utc_timestamp(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == UTC.utcoffset(None)
    )


def _contains_prohibited_metadata_term(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    return any(term in normalized for term in _PROHIBITED_METADATA_TERMS)


def delivery_payload_digest(intent: object) -> tuple[str, str | None]:
    """Return a safe digest and any pre-serialization rejection reason."""

    reason_code = preflight_intent_rejection(intent)
    if reason_code is not None:
        return _rejection_payload_digest(intent, reason_code), reason_code
    try:
        return intent.canonical_payload_digest, None
    except (AttributeError, TypeError, ValueError):
        reason_code = "invalid_intent_payload"
        return _rejection_payload_digest(intent, reason_code), reason_code


def _rejection_payload_digest(intent: object, reason_code: str) -> str:
    metadata = getattr(intent, "metadata", None)
    intent_id = (
        str(metadata.intent_id)
        if isinstance(metadata, IntentMetadata) and isinstance(metadata.intent_id, IntentId)
        else "invalid_intent_id"
    )
    kind = getattr(getattr(intent, "kind", None), "value", "invalid_intent_kind")
    descriptor = {
        "intent_id": intent_id,
        "kind": kind,
        "rejection_reason": reason_code,
        "metadata": _rejection_metadata_descriptor(metadata),
    }
    return sha256(canonical_bytes(descriptor)).hexdigest()


def _rejection_metadata_descriptor(metadata: object) -> dict[str, object]:
    if not isinstance(metadata, IntentMetadata):
        return {"invalid_metadata": True}
    return {
        "source_attempt": (
            str(metadata.source_attempt_id)
            if isinstance(metadata.source_attempt_id, AttemptId)
            else "invalid_or_none"
        ),
        "evidence_refs": [
            _artifact_payload(reference)
            if isinstance(reference, ArtifactRef)
            else {"invalid_evidence_ref": type(reference).__name__}
            for reference in metadata.evidence_refs
        ],
        "safe_metadata_values": sorted(
            _safe_metadata_value_descriptor(value) for value in metadata.safe_metadata.values()
        ),
    }


def _safe_metadata_value_descriptor(value: object) -> str:
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, int):
        return f"int:{value}"
    if isinstance(value, float):
        if value != value:
            return "float:nan"
        if value == float("inf"):
            return "float:positive_infinity"
        if value == float("-inf"):
            return "float:negative_infinity"
        return f"float:{value!r}"
    return f"invalid:{type(value).__name__}"


@dataclass(frozen=True, slots=True)
class IntentAck:
    intent_id: IntentId
    canonical_payload_digest: str
    kind: LedgerIntentKind
    idempotent: bool = False


@dataclass(frozen=True, slots=True)
class IntentRejection:
    intent_id: IntentId
    canonical_payload_digest: str
    kind: LedgerIntentKind
    reason_code: str
    idempotent: bool = False


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    sequence: int
    intent_id: IntentId
    canonical_payload_digest: str
    kind: LedgerIntentKind
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RejectedIntentEvidence:
    intent_id: IntentId
    canonical_payload_digest: str
    kind: LedgerIntentKind
    reason_code: str
    occurred_at: datetime
