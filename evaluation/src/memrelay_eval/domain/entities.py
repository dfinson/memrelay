"""Frozen treatment-neutral domain records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType

from .errors import InvalidArtifactManifestError, InvalidAttemptTerminalError
from .ids import (
    ArtifactId,
    AssignmentId,
    AttemptId,
    ClaimId,
    CostEntryId,
    EndpointId,
    EvidenceId,
    ExperimentId,
    HistoryId,
    InclusionId,
    ProtocolId,
    RetentionPolicyId,
    RunId,
    ScenarioId,
    TaskId,
)
from .policies import validate_run_transition
from .states import ArtifactScope, AttemptTerminalKind, InclusionStatus, RunState

_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _utc_z(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidArtifactManifestError("timestamps must be timezone-aware UTC values")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Experiment:
    id: ExperimentId
    protocol_id: ProtocolId


@dataclass(frozen=True, slots=True)
class Protocol:
    id: ProtocolId


@dataclass(frozen=True, slots=True)
class Scenario:
    id: ScenarioId


@dataclass(frozen=True, slots=True)
class Task:
    id: TaskId


@dataclass(frozen=True, slots=True)
class History:
    id: HistoryId


@dataclass(frozen=True, slots=True)
class Assignment:
    id: AssignmentId
    experiment_id: ExperimentId


@dataclass(frozen=True, slots=True)
class Run:
    id: RunId
    assignment_id: AssignmentId


@dataclass(frozen=True, slots=True)
class Attempt:
    id: AttemptId
    run_id: RunId


@dataclass(frozen=True, slots=True)
class Evidence:
    id: EvidenceId
    artifact_ids: tuple[ArtifactId, ...]


@dataclass(frozen=True, slots=True)
class Endpoint:
    id: EndpointId


@dataclass(frozen=True, slots=True)
class Claim:
    id: ClaimId


@dataclass(frozen=True, slots=True)
class CostEntry:
    id: CostEntryId


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Immutable content-addressed bytes."""

    artifact_id: ArtifactId
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.sha256):
            raise InvalidArtifactManifestError("sha256 must be a lowercase SHA-256 digest")
        if self.size_bytes < 0:
            raise InvalidArtifactManifestError("size_bytes must not be negative")
        if self.artifact_id != ArtifactId.from_digest(self.sha256):
            raise InvalidArtifactManifestError("artifact_id must be derived from sha256")

    @classmethod
    def from_bytes(cls, data: bytes) -> ArtifactRef:
        digest = sha256(data).hexdigest()
        return cls(ArtifactId.from_digest(digest), digest, len(data))


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """Version 1.0.0 projection for immutable artifact metadata."""

    artifact_id: ArtifactId
    kind: str
    sha256: str
    size_bytes: int
    media_type: str
    created_at: datetime
    producer_component: str
    producer_version: str
    classification: str
    contains_secrets: bool
    source_artifact_ids: tuple[ArtifactId, ...]
    retention_policy_id: RetentionPolicyId
    encryption: Mapping[str, str] | None
    scope: ArtifactScope
    experiment_id: ExperimentId | None = None
    run_id: RunId | None = None
    attempt_id: AttemptId | None = None
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise InvalidArtifactManifestError("unsupported artifact manifest schema version")
        if not _SHA256.fullmatch(self.sha256):
            raise InvalidArtifactManifestError("sha256 must be a lowercase SHA-256 digest")
        if self.artifact_id != ArtifactId.from_digest(self.sha256):
            raise InvalidArtifactManifestError("artifact_id must be derived from sha256")
        if self.size_bytes < 0 or not self.kind or not self.media_type:
            raise InvalidArtifactManifestError(
                "kind, media_type, and non-negative size are required"
            )
        _utc_z(self.created_at)
        if not self.producer_component or not self.producer_version or not self.classification:
            raise InvalidArtifactManifestError("producer and classification are required")
        if self.scope is ArtifactScope.EXPERIMENT:
            if self.experiment_id is None or self.run_id is not None or self.attempt_id is not None:
                raise InvalidArtifactManifestError(
                    "experiment artifacts require only experiment_id"
                )
        elif self.scope is ArtifactScope.RUN:
            if self.run_id is None or self.attempt_id is not None:
                raise InvalidArtifactManifestError("run artifacts require run_id and no attempt_id")
        elif self.scope is ArtifactScope.ATTEMPT:
            if self.run_id is None or self.attempt_id is None:
                raise InvalidArtifactManifestError(
                    "attempt artifacts require run_id and attempt_id"
                )
        else:
            raise InvalidArtifactManifestError("unsupported artifact scope")
        if self.encryption is not None:
            object.__setattr__(self, "encryption", MappingProxyType(dict(self.encryption)))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": str(self.artifact_id),
            "kind": self.kind,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "created_at": _utc_z(self.created_at),
            "producer": {"component": self.producer_component, "version": self.producer_version},
            "classification": self.classification,
            "contains_secrets": self.contains_secrets,
            "source_artifact_ids": [str(value) for value in self.source_artifact_ids],
            "retention_policy_id": str(self.retention_policy_id),
            "encryption": dict(self.encryption) if self.encryption is not None else None,
            "scope": self.scope.value,
            "experiment_id": str(self.experiment_id) if self.experiment_id else None,
            "run_id": str(self.run_id) if self.run_id else None,
            "attempt_id": str(self.attempt_id) if self.attempt_id else None,
        }


@dataclass(frozen=True, slots=True)
class RunTransition:
    run_id: RunId
    previous: RunState
    next_state: RunState
    occurred_at: datetime

    def __post_init__(self) -> None:
        validate_run_transition(self.previous, self.next_state)
        _utc_z(self.occurred_at)


@dataclass(frozen=True, slots=True)
class AttemptTerminal:
    attempt_id: AttemptId
    run_id: RunId
    classification: AttemptTerminalKind
    occurred_at: datetime
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.classification, AttemptTerminalKind):
            raise InvalidAttemptTerminalError(
                "classification must use the frozen terminal vocabulary"
            )
        _utc_z(self.occurred_at)


@dataclass(frozen=True, slots=True)
class ArtifactLink:
    """Authoritative ledger ownership link for pre-attempt and attempt evidence."""

    artifact_ref: ArtifactRef
    purpose: str
    experiment_id: ExperimentId | None = None
    run_id: RunId | None = None
    attempt_id: AttemptId | None = None

    def __post_init__(self) -> None:
        if not self.purpose:
            raise InvalidArtifactManifestError("artifact links require a purpose")
        if self.attempt_id is not None and self.run_id is None:
            raise InvalidArtifactManifestError("attempt artifact links require run_id")
        if self.experiment_id is None and self.run_id is None:
            raise InvalidArtifactManifestError("artifact links require experiment or run ownership")


@dataclass(frozen=True, slots=True)
class InclusionDecision:
    id: InclusionId
    run_id: RunId
    status: InclusionStatus
    reason: str
    reconciliation_sha256: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.reconciliation_sha256):
            raise InvalidArtifactManifestError(
                "reconciliation_sha256 must be a lowercase SHA-256 digest"
            )
        _utc_z(self.occurred_at)


@dataclass(frozen=True, slots=True)
class TelemetryObservation:
    event_name: str
    occurred_at: datetime
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _utc_z(self.occurred_at)
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """The non-secret identity of the SDK and runtime execution substrate."""

    sdk_version: str
    wheel_filename: str
    wheel_sha256: str
    runtime_version: str
    runtime_sha256: str
    transport: str
    auth_mode: str
    subscription_identity_sha256: str

    def __post_init__(self) -> None:
        if not self.sdk_version or not self.runtime_version or not self.transport:
            raise ValueError("runtime identity requires version and transport fields")
        if self.auth_mode != "copilot_subscription":
            raise ValueError("only current Copilot subscription authentication is supported")
        if self.wheel_filename != "github_copilot_sdk-1.0.8-py3-none-any.whl":
            raise ValueError("the Copilot SDK wheel filename is frozen")
        for digest in (
            self.wheel_sha256,
            self.runtime_sha256,
            self.subscription_identity_sha256,
        ):
            if not _SHA256.fullmatch(digest):
                raise ValueError("runtime identity hashes must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class NativeModel:
    """A model exactly as reported by the native Copilot catalog."""

    native_id: str
    family: str
    capabilities: Mapping[str, object]
    reasoning_effort: object
    context_tier: object

    def __post_init__(self) -> None:
        if not self.native_id:
            raise ValueError("native model IDs are required")
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities)))


@dataclass(frozen=True, slots=True)
class NativeModelCatalog:
    """Immutable raw and projected model-catalog evidence."""

    raw_sha256: str
    projection_sha256: str
    models: tuple[NativeModel, ...]

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.raw_sha256) or not _SHA256.fullmatch(self.projection_sha256):
            raise ValueError("catalog hashes must be lowercase SHA-256")
        if len({model.native_id for model in self.models}) != len(self.models):
            raise ValueError("native model IDs must be unique")


@dataclass(frozen=True, slots=True)
class QualificationCaps:
    """Frozen aggregate limits for a finite, explicit qualification invocation."""

    session_limit: int
    credit_limit: float | None
    token_limit: int | None
    active_seconds_limit: float | None
    wall_seconds_limit: float | None

    def __post_init__(self) -> None:
        if self.session_limit < 1:
            raise ValueError("qualification session_limit must be positive")
        for value in (
            self.credit_limit,
            self.token_limit,
            self.active_seconds_limit,
            self.wall_seconds_limit,
        ):
            if value is None or value < 0:
                raise ValueError("qualification caps must be explicit non-negative values")


@dataclass(frozen=True, slots=True)
class QualificationUsage:
    sessions: int = 0
    credits: float = 0.0
    tokens: int = 0
    active_seconds: float = 0.0
    wall_seconds: float = 0.0

    def __post_init__(self) -> None:
        if (
            self.sessions < 0
            or self.credits < 0
            or self.tokens < 0
            or self.active_seconds < 0
            or self.wall_seconds < 0
        ):
            raise ValueError("qualification usage cannot be negative")

    def plus(self, other: QualificationUsage) -> QualificationUsage:
        return QualificationUsage(
            sessions=self.sessions + other.sessions,
            credits=self.credits + other.credits,
            tokens=self.tokens + other.tokens,
            active_seconds=self.active_seconds + other.active_seconds,
            wall_seconds=self.wall_seconds + other.wall_seconds,
        )


@dataclass(frozen=True, slots=True)
class QualificationTaskResult:
    """One nonstudy task session; failures are evidence and are never retried."""

    executable_passed: bool
    protected_check_fraction: float
    usage: QualificationUsage

    def __post_init__(self) -> None:
        if not 0.0 <= self.protected_check_fraction <= 1.0:
            raise ValueError("protected_check_fraction must be in [0, 1]")
        if self.usage.sessions != 1:
            raise ValueError("each qualification result represents exactly one session")


@dataclass(frozen=True, slots=True)
class ModelQualification:
    """Complete eight-task result for one native model."""

    native_id: str
    task_results: tuple[QualificationTaskResult, ...]

    def __post_init__(self) -> None:
        if len(self.task_results) != 8:
            raise ValueError(
                "each eligible model must receive exactly eight qualification sessions"
            )

    @property
    def executable_passes(self) -> int:
        return sum(result.executable_passed for result in self.task_results)

    @property
    def protected_check_fraction(self) -> float:
        return sum(result.protected_check_fraction for result in self.task_results) / 8

    @property
    def median_active_seconds(self) -> float:
        values = sorted(result.usage.active_seconds for result in self.task_results)
        return (values[3] + values[4]) / 2

    @property
    def median_credits(self) -> float:
        values = sorted(result.usage.credits for result in self.task_results)
        return (values[3] + values[4]) / 2

    @property
    def usage(self) -> QualificationUsage:
        total = QualificationUsage()
        for result in self.task_results:
            total = total.plus(result.usage)
        return total


@dataclass(frozen=True, slots=True)
class LockedModel:
    """A selected model's exact native catalog properties."""

    role: str
    native_id: str
    family: str
    capabilities: Mapping[str, object]
    reasoning_effort: object
    context_tier: object

    def __post_init__(self) -> None:
        if self.role not in {"M0", "M1", "M2", "judge"}:
            raise ValueError("model lock role is invalid")
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities)))
