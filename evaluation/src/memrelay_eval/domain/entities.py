"""Frozen treatment-neutral domain records."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType

from .errors import InvalidArtifactManifestError, InvalidAttemptTerminalError
from .ids import (
    AnalysisId,
    ArtifactId,
    AssignmentId,
    AttemptId,
    ClaimId,
    ConfigurationId,
    CostEntryId,
    EndpointId,
    EnrollmentPlanId,
    EnvironmentStratumId,
    EpisodeId,
    EvidenceId,
    ExperimentId,
    HistoryId,
    InclusionId,
    ProtocolId,
    ReportId,
    RetentionPolicyId,
    RunId,
    ScenarioId,
    SequenceId,
    StratumId,
    TaskId,
)
from .policies import validate_run_transition
from .states import (
    ArtifactScope,
    AttemptTerminalKind,
    EvaluationStratum,
    ExposureClassification,
    ExposurePhase,
    GraderTerminalKind,
    HistoryMode,
    InclusionStatus,
    InternalRetrySubsystem,
    ProbeWriteDisposition,
    RunState,
    SequenceState,
)

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
    allows_pre_exposure_infrastructure_retry: bool = False


@dataclass(frozen=True, slots=True)
class DynamicSequence:
    """Public, arm-neutral record for a wholly assigned dynamic history."""

    id: SequenceId
    experiment_id: ExperimentId
    assignment_id: AssignmentId
    assignment_plan_hash: str
    episode_ids: tuple[EpisodeId, ...]
    history_mode: HistoryMode
    stratum: EvaluationStratum
    allocation_seed_commitment: str

    def __post_init__(self) -> None:
        if self.history_mode is not HistoryMode.DYNAMIC:
            raise InvalidArtifactManifestError("dynamic sequence must use dynamic history mode")
        if not _SHA256.fullmatch(self.assignment_plan_hash) or not _SHA256.fullmatch(
            self.allocation_seed_commitment
        ):
            raise InvalidArtifactManifestError("dynamic sequence commitments must be SHA-256")
        if not self.episode_ids or len(set(self.episode_ids)) != len(self.episode_ids):
            raise InvalidArtifactManifestError(
                "dynamic sequence episode order must be unique and non-empty"
            )
        object.__setattr__(self, "episode_ids", tuple(self.episode_ids))


@dataclass(frozen=True, slots=True)
class DynamicEpisode:
    """Arm-neutral episode lineage; its predecessors are immutable terminal attempts."""

    sequence_id: SequenceId
    episode_id: EpisodeId
    ordinal: int
    prior_terminal_attempt_ids: tuple[AttemptId, ...]

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise InvalidArtifactManifestError("episode ordinal must not be negative")
        if len(set(self.prior_terminal_attempt_ids)) != len(self.prior_terminal_attempt_ids):
            raise InvalidArtifactManifestError("episode prior terminal lineage must not duplicate")
        object.__setattr__(
            self, "prior_terminal_attempt_ids", tuple(self.prior_terminal_attempt_ids)
        )


@dataclass(frozen=True, slots=True)
class DynamicSequenceTerminal:
    """The deterministic terminal projection retained for a complete sequence."""

    sequence_id: SequenceId
    state: SequenceState
    terminal_attempt_ids: tuple[AttemptId, ...]
    evidence_refs: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        if self.state is not SequenceState.TERMINAL:
            raise InvalidAttemptTerminalError("sequence terminal must use terminal state")
        if not self.terminal_attempt_ids:
            raise InvalidAttemptTerminalError(
                "sequence terminal requires attempted episode lineage"
            )
        object.__setattr__(self, "terminal_attempt_ids", tuple(self.terminal_attempt_ids))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class DynamicSequenceCleanup:
    """Typed, append-only cleanup completion after a sequence terminal."""

    sequence_id: SequenceId
    state: SequenceState
    evidence_refs: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        if self.state is not SequenceState.CLEANED_UP:
            raise InvalidAttemptTerminalError("sequence cleanup must use cleaned_up state")
        if not self.evidence_refs:
            raise InvalidAttemptTerminalError("sequence cleanup requires evidence")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


GOLDEN_SETUP_PROVENANCE = "golden_setup_source"
"""The sole provenance a controlled-history item may declare; never treatment-generated."""


@dataclass(frozen=True, slots=True)
class ControlledHistoryItem:
    """One immutable, ordered pre-treatment episode inside a controlled bundle."""

    position: int
    artifact: ArtifactRef
    actor_id: str
    scope: str
    revision: str
    provenance: str
    valid_from: datetime
    valid_to: datetime | None
    expected_graph_input_sha256: str

    def __post_init__(self) -> None:
        if self.position < 1:
            raise InvalidArtifactManifestError("controlled history item position must start at 1")
        if not self.actor_id or not self.scope or not self.revision:
            raise InvalidArtifactManifestError(
                "controlled history item requires actor_id, scope, and revision"
            )
        if self.provenance != GOLDEN_SETUP_PROVENANCE:
            raise InvalidArtifactManifestError(
                "controlled history items must declare golden-setup provenance only; "
                "no treatment-generated content may enter the source bundle"
            )
        _utc_z(self.valid_from)
        if self.valid_to is not None:
            _utc_z(self.valid_to)
            if self.valid_to < self.valid_from:
                raise InvalidArtifactManifestError(
                    "controlled history item validity window is inverted"
                )
        if not _SHA256.fullmatch(self.expected_graph_input_sha256):
            raise InvalidArtifactManifestError(
                "expected graph input hash must be a lowercase SHA-256 digest"
            )

    def to_record(self) -> dict[str, object]:
        return {
            "position": self.position,
            "artifact_sha256": self.artifact.sha256,
            "actor_id": self.actor_id,
            "scope": self.scope,
            "revision": self.revision,
            "provenance": self.provenance,
            "valid_from": _utc_z(self.valid_from),
            "valid_to": _utc_z(self.valid_to) if self.valid_to is not None else None,
            "expected_graph_input_sha256": self.expected_graph_input_sha256,
        }


@dataclass(frozen=True, slots=True)
class ControlledHistoryBundle:
    """The frozen, immutable pre-treatment bundle shared by every controlled arm."""

    history_id: HistoryId
    protocol_id: ProtocolId
    stratum: EvaluationStratum
    ordered_items: tuple[ControlledHistoryItem, ...]
    content_sha256: str
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise InvalidArtifactManifestError(
                "unsupported controlled history bundle schema version"
            )
        if not self.ordered_items:
            raise InvalidArtifactManifestError("controlled history bundle requires ordered items")
        object.__setattr__(self, "ordered_items", tuple(self.ordered_items))
        positions = [item.position for item in self.ordered_items]
        if positions != list(range(1, len(positions) + 1)):
            raise InvalidArtifactManifestError(
                "controlled history items must be contiguously ordered starting at 1"
            )
        if not _SHA256.fullmatch(self.content_sha256):
            raise InvalidArtifactManifestError(
                "controlled history bundle digest must be a lowercase SHA-256 digest"
            )

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "history_id": str(self.history_id),
            "protocol_id": str(self.protocol_id),
            "mode": HistoryMode.CONTROLLED.value,
            "stratum": self.stratum.value,
            "ordered_items": [item.to_record() for item in self.ordered_items],
        }


@dataclass(frozen=True, slots=True)
class ControlledProbeWritePolicy:
    """The frozen, protocol-declared probe-write handling for one controlled history."""

    history_id: HistoryId
    disposition: ProbeWriteDisposition

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, ProbeWriteDisposition):
            raise InvalidArtifactManifestError(
                "controlled probe-write policy requires a frozen disposition"
            )


@dataclass(frozen=True, slots=True)
class ControlledRestoreManifest:
    """Immutable, per-attempt evidence that a restore produced byte-identical inputs."""

    attempt_id: AttemptId
    history_id: HistoryId
    bundle_content_sha256: str
    restored_content_sha256: str
    parity_hash: str
    verified_at: datetime

    def __post_init__(self) -> None:
        for digest in (
            self.bundle_content_sha256,
            self.restored_content_sha256,
            self.parity_hash,
        ):
            if not _SHA256.fullmatch(digest):
                raise InvalidArtifactManifestError(
                    "controlled restore manifest digests must be lowercase SHA-256"
                )
        _utc_z(self.verified_at)

    @property
    def is_byte_identical(self) -> bool:
        return self.restored_content_sha256 == self.bundle_content_sha256

    def to_record(self) -> dict[str, object]:
        return {
            "attempt_id": str(self.attempt_id),
            "history_id": str(self.history_id),
            "bundle_content_sha256": self.bundle_content_sha256,
            "restored_content_sha256": self.restored_content_sha256,
            "parity_hash": self.parity_hash,
            "verified_at": _utc_z(self.verified_at),
            "byte_identical": self.is_byte_identical,
        }


@dataclass(frozen=True, slots=True)
class ControlledAnalysisIdentity:
    """Identity required to aggregate controlled-effect estimands without pooling."""

    history_mode: HistoryMode
    stratum: EvaluationStratum
    history_content_sha256: str

    def __post_init__(self) -> None:
        if self.history_mode is not HistoryMode.CONTROLLED:
            raise InvalidArtifactManifestError(
                "controlled analysis identity requires controlled history mode"
            )
        if not _SHA256.fullmatch(self.history_content_sha256):
            raise InvalidArtifactManifestError(
                "controlled analysis identity digest must be lowercase SHA-256"
            )


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
class CostLedgerLink:
    """Thin query projection for one immutable quantity artifact."""

    cost_entry_id: CostEntryId
    run_id: RunId
    attempt_id: AttemptId
    logical_ledger: str
    artifact_ref: ArtifactRef


@dataclass(frozen=True, slots=True)
class MonetaryViewLink:
    """Thin query projection for one immutable derived monetary-view artifact."""

    monetary_view_id: str
    run_id: RunId
    attempt_id: AttemptId
    category: str
    artifact_ref: ArtifactRef
    price_table_ref: ArtifactRef


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


class GraderSandboxDiagnosticAuthority(StrEnum):
    """The bounded authority that observed a confined-grader failure."""

    SANDBOX = "sandbox"


class GraderSandboxDiagnosticPhase(StrEnum):
    """A non-overlapping stage in confined grader execution."""

    SELECTION_PROBE = "selection_probe"
    PROFILE_LAUNCH = "profile_launch"
    CANDIDATE_RUNTIME = "candidate_runtime"
    OUTPUT_PARSE = "output_parse"


class GraderSandboxDiagnosticCode(StrEnum):
    """Value-free failure codes for the confined grader diagnostic matrix."""

    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    PROFILE_LAUNCH_FAILED = "profile_launch_failed"
    TIMEOUT = "timeout"
    CRASH = "crash"
    MALFORMED_OUTPUT = "malformed_output"


@dataclass(frozen=True, slots=True)
class GraderSandboxDiagnostic:
    """A closed, value-free projection of a sandbox failure."""

    authority: GraderSandboxDiagnosticAuthority
    phase: GraderSandboxDiagnosticPhase
    code: GraderSandboxDiagnosticCode

    def __post_init__(self) -> None:
        if (
            not isinstance(self.authority, GraderSandboxDiagnosticAuthority)
            or not isinstance(self.phase, GraderSandboxDiagnosticPhase)
            or not isinstance(self.code, GraderSandboxDiagnosticCode)
        ):
            raise InvalidArtifactManifestError("grader sandbox diagnostic fields must be typed")
        valid_pairs = {
            (
                GraderSandboxDiagnosticPhase.SELECTION_PROBE,
                GraderSandboxDiagnosticCode.AUTHORITY_UNAVAILABLE,
            ),
            (
                GraderSandboxDiagnosticPhase.PROFILE_LAUNCH,
                GraderSandboxDiagnosticCode.PROFILE_LAUNCH_FAILED,
            ),
            (
                GraderSandboxDiagnosticPhase.CANDIDATE_RUNTIME,
                GraderSandboxDiagnosticCode.TIMEOUT,
            ),
            (
                GraderSandboxDiagnosticPhase.CANDIDATE_RUNTIME,
                GraderSandboxDiagnosticCode.CRASH,
            ),
            (
                GraderSandboxDiagnosticPhase.OUTPUT_PARSE,
                GraderSandboxDiagnosticCode.MALFORMED_OUTPUT,
            ),
        }
        if (
            self.authority is not GraderSandboxDiagnosticAuthority.SANDBOX
            or (
                self.phase,
                self.code,
            )
            not in valid_pairs
        ):
            raise InvalidArtifactManifestError("grader sandbox diagnostic matrix is invalid")

    def to_record(self) -> dict[str, str]:
        return {
            "schema_version": "1.0.0",
            "authority": self.authority.value,
            "phase": self.phase.value,
            "code": self.code.value,
        }


def _normalized_grader_path(path: str) -> str:
    if not isinstance(path, str):
        raise InvalidArtifactManifestError("grader path rules must be strings")
    normalized = unicodedata.normalize("NFC", path).replace("\\", "/")
    if not normalized or normalized.startswith("/") or ":" in normalized:
        raise InvalidArtifactManifestError("grader path rules must be relative")
    segments = normalized.split("/")
    if any(
        not segment or segment in {".", ".."} or segment.endswith((" ", "."))
        for segment in segments
    ):
        raise InvalidArtifactManifestError("grader path rules contain an ambiguous alias")
    return "/".join(segment.casefold() for segment in segments)


@dataclass(frozen=True, slots=True)
class GraderContract:
    """Pinned, treatment-blind executable-grader inputs and restrictions."""

    grader_version: str
    grader_sha256: str
    native_tests_artifact: ArtifactRef
    hidden_tests_artifact: ArtifactRef
    dependencies_artifact: ArtifactRef
    scope_policy_sha256: str
    tamper_policy_sha256: str
    command: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    expected_baseline_revision: str
    expected_baseline_files_sha256: str
    network_policy: str = "deny"
    maximum_regrades: int = 0
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0" or not self.grader_version:
            raise InvalidArtifactManifestError("grader contract schema and version are required")
        for digest in (
            self.grader_sha256,
            self.scope_policy_sha256,
            self.tamper_policy_sha256,
            self.expected_baseline_files_sha256,
        ):
            if not _SHA256.fullmatch(digest):
                raise InvalidArtifactManifestError(
                    "grader contract digests must be lowercase SHA-256"
                )
        if not self.expected_baseline_revision or self.network_policy != "deny":
            raise InvalidArtifactManifestError("grader contract requires a deny network policy")
        if self.maximum_regrades < 0:
            raise InvalidArtifactManifestError(
                "grader contract maximum regrades cannot be negative"
            )
        command = tuple(self.command)
        if not command or command[0] != "{python}":
            raise InvalidArtifactManifestError(
                "grader command must begin with the pinned Python placeholder"
            )
        normalized_allowed = tuple(_normalized_grader_path(path) for path in self.allowed_paths)
        normalized_forbidden = tuple(_normalized_grader_path(path) for path in self.forbidden_paths)
        if len(set(normalized_allowed)) != len(normalized_allowed) or len(
            set(normalized_forbidden)
        ) != len(normalized_forbidden):
            raise InvalidArtifactManifestError("grader path rules must not duplicate")
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "allowed_paths", tuple(self.allowed_paths))
        object.__setattr__(self, "forbidden_paths", tuple(self.forbidden_paths))

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "grader_version": self.grader_version,
            "grader_sha256": self.grader_sha256,
            "native_tests_sha256": self.native_tests_artifact.sha256,
            "hidden_tests_sha256": self.hidden_tests_artifact.sha256,
            "dependencies_sha256": self.dependencies_artifact.sha256,
            "scope_policy_sha256": self.scope_policy_sha256,
            "tamper_policy_sha256": self.tamper_policy_sha256,
            "command": list(self.command),
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "expected_baseline_revision": self.expected_baseline_revision,
            "expected_baseline_files_sha256": self.expected_baseline_files_sha256,
            "network_policy": self.network_policy,
            "maximum_regrades": self.maximum_regrades,
        }


@dataclass(frozen=True, slots=True)
class GraderResult:
    """Immutable normalized executable result; raw output stays in its artifact."""

    snapshot_sha256: str
    contract_sha256: str
    terminal: GraderTerminalKind
    binary_passed: bool | None
    test_outcomes: Mapping[str, bool]
    continuous_score: float | None
    objective_components: Mapping[str, float]
    raw_output_artifact: ArtifactRef | None
    result_artifact: ArtifactRef
    evidence_refs: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        for digest in (self.snapshot_sha256, self.contract_sha256):
            if not _SHA256.fullmatch(digest):
                raise InvalidArtifactManifestError(
                    "grader result digests must be lowercase SHA-256"
                )
        if self.terminal is GraderTerminalKind.PASSED and self.binary_passed is not True:
            raise InvalidArtifactManifestError("passed grader results require a binary pass")
        if self.terminal is GraderTerminalKind.FAILED and self.binary_passed is not False:
            raise InvalidArtifactManifestError("failed grader results require a binary failure")
        if self.terminal in {GraderTerminalKind.UNAVAILABLE, GraderTerminalKind.BLOCKED} and (
            self.binary_passed is not None
        ):
            raise InvalidArtifactManifestError("blocked or unavailable grades cannot infer a pass")
        if self.continuous_score is not None and not isinstance(self.continuous_score, float):
            raise InvalidArtifactManifestError("continuous scores must be floats when available")
        if any(
            not isinstance(name, str) or not isinstance(value, bool)
            for name, value in self.test_outcomes.items()
        ):
            raise InvalidArtifactManifestError("test outcomes must be named booleans")
        if any(
            not isinstance(name, str) or not isinstance(value, float)
            for name, value in self.objective_components.items()
        ):
            raise InvalidArtifactManifestError("objective components must be named floats")
        object.__setattr__(self, "test_outcomes", MappingProxyType(dict(self.test_outcomes)))
        object.__setattr__(
            self, "objective_components", MappingProxyType(dict(self.objective_components))
        )
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class FlakyTestClassification:
    """A frozen aggregate from bounded candidate reruns, never a favorable selection."""

    outcomes: tuple[bool, ...]
    preregistered_signature: tuple[bool, ...] | None

    def __post_init__(self) -> None:
        if not self.outcomes or len(self.outcomes) > 3:
            raise InvalidArtifactManifestError(
                "candidate grading has one initial run and at most two reruns"
            )
        if self.preregistered_signature is not None and (
            len(self.preregistered_signature) != len(self.outcomes)
        ):
            raise InvalidArtifactManifestError("flaky signature must cover every candidate grading")
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        if self.preregistered_signature is not None:
            object.__setattr__(self, "preregistered_signature", tuple(self.preregistered_signature))

    @property
    def is_preregistered_flaky_signature(self) -> bool:
        return self.preregistered_signature is not None and (
            self.outcomes == self.preregistered_signature
        )

    @property
    def aggregate_passed(self) -> bool:
        return all(self.outcomes)


@dataclass(frozen=True, slots=True)
class FrozenArtifactInput:
    """The immutable identity and non-secret provenance of one enrollment input."""

    artifact: ArtifactRef
    schema_version: str
    provenance: str

    def __post_init__(self) -> None:
        if not self.schema_version or not self.provenance:
            raise InvalidArtifactManifestError(
                "frozen input schema version and provenance are required"
            )

    def to_record(self) -> dict[str, str]:
        return {
            "artifact_identity": str(self.artifact.artifact_id),
            "schema_version": self.schema_version,
            "digest": self.artifact.sha256,
            "provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class EffectiveConfigurationArtifact:
    """Hash-addressed, redacted configuration evidence for an enrollment attempt."""

    id: ConfigurationId
    artifact: ArtifactRef
    document_digest: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.document_digest):
            raise InvalidArtifactManifestError(
                "effective configuration digest must be a lowercase SHA-256 digest"
            )
        if self.id != ConfigurationId.from_digest(self.document_digest):
            raise InvalidArtifactManifestError(
                "configuration identity must be derived from its document digest"
            )

    def to_frozen_input(self) -> FrozenArtifactInput:
        return FrozenArtifactInput(
            self.artifact, schema_version="1.0.0", provenance="effective_configuration"
        )


@dataclass(frozen=True, slots=True)
class EnvironmentStratum:
    """A deterministic identity that prevents pooling across changed hosts."""

    id: EnvironmentStratumId
    fingerprint_digest: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.fingerprint_digest):
            raise InvalidArtifactManifestError(
                "environment fingerprint digest must be a lowercase SHA-256 digest"
            )
        if self.id != EnvironmentStratumId.from_digest(self.fingerprint_digest):
            raise InvalidArtifactManifestError(
                "environment stratum identity must derive from the fingerprint digest"
            )


@dataclass(frozen=True, slots=True)
class EnrollmentPlanLineage:
    """Immutable relation from a replacement plan to the plan it supersedes."""

    parent_plan_id: EnrollmentPlanId
    reason: str
    replacement_attempt_id: AttemptId | None = None

    def __post_init__(self) -> None:
        if self.reason not in {"protocol_revision", "attempt_configuration_revision"}:
            raise InvalidArtifactManifestError("enrollment lineage reason is invalid")
        if self.reason == "attempt_configuration_revision" and self.replacement_attempt_id is None:
            raise InvalidArtifactManifestError(
                "configuration revisions require a replacement attempt identity"
            )

    def to_record(self) -> dict[str, str]:
        record = {
            "parent_plan_id": str(self.parent_plan_id),
            "reason": self.reason,
        }
        if self.replacement_attempt_id is not None:
            record["replacement_attempt_id"] = str(self.replacement_attempt_id)
        return record


@dataclass(frozen=True, slots=True)
class EnrollmentPlan:
    """A treatment-neutral pre-assignment seal over immutable input artifacts."""

    id: EnrollmentPlanId
    artifact: ArtifactRef
    document_digest: str
    inputs: Mapping[str, FrozenArtifactInput]
    parity_inputs_digest: str
    lineage: EnrollmentPlanLineage | None = None
    unpaid_conformance: bool = False

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.document_digest) or not _SHA256.fullmatch(
            self.parity_inputs_digest
        ):
            raise InvalidArtifactManifestError("enrollment plan digests must be lowercase SHA-256")
        if self.id != EnrollmentPlanId.from_digest(self.document_digest):
            raise InvalidArtifactManifestError(
                "enrollment plan identity must derive from its document digest"
            )
        expected = {
            "catalog",
            "protocol",
            "effective_configuration",
            "environment_fingerprint",
            "native_model_catalog",
            "assignment_algorithm",
            "seed_commitment",
            "blocks",
            "ordered_inputs",
            "price_tables",
            "eligibility_dispositions",
        }
        if set(self.inputs) != expected:
            raise InvalidArtifactManifestError("enrollment plan input set is incomplete")
        object.__setattr__(self, "inputs", MappingProxyType(dict(self.inputs)))


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
        if len(set(self.source_artifact_ids)) != len(self.source_artifact_ids):
            raise InvalidArtifactManifestError("source_artifact_ids must not contain duplicates")
        if self.encryption is not None and (
            not self.encryption
            or any(
                not isinstance(key, str) or not isinstance(value, str) or not key or not value
                for key, value in self.encryption.items()
            )
        ):
            raise InvalidArtifactManifestError(
                "encryption metadata must be a non-empty string mapping"
            )
        if self.contains_secrets and self.encryption is None:
            raise InvalidArtifactManifestError(
                "secret-bearing artifacts require explicit encryption metadata"
            )
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
    evidence_refs: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.classification, AttemptTerminalKind):
            raise InvalidAttemptTerminalError(
                "classification must use the frozen terminal vocabulary"
            )
        _utc_z(self.occurred_at)
        if not self.reason:
            raise InvalidAttemptTerminalError("terminal records require a typed reason")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class ExposureDecision:
    """Conservative exposure evidence supplied by the owning exposure contract."""

    classification: ExposureClassification
    evidence_refs: tuple[ArtifactRef, ...] = ()
    reason_code: str = "legacy_exposure_decision"
    first_monotonic_exposure_time: float | None = None
    observations: tuple[ExposureObservation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "observations", tuple(self.observations))

    @property
    def is_conclusively_unexposed(self) -> bool:
        return (
            self.classification is ExposureClassification.UNEXPOSED
            and self.reason_code in {"conclusively_unexposed", "legacy_exposure_decision"}
            and bool(self.evidence_refs)
        )


@dataclass(frozen=True, slots=True)
class ExposureObservation:
    """One evidence-backed observation at a lifecycle exposure boundary."""

    phase: ExposurePhase
    observed: bool
    occurred_at: datetime
    monotonic_seconds: float
    evidence_refs: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class ExposureRecord:
    """Append-only attempt evidence used to govern retry eligibility."""

    attempt_id: AttemptId
    assignment_id: AssignmentId
    decision: ExposureDecision


@dataclass(frozen=True, slots=True)
class RetryAuthorization:
    """Immutable lineage from one terminal attempt to its sole retry attempt."""

    run_id: RunId
    assignment_id: AssignmentId
    parent_assignment_id: AssignmentId
    parent_attempt_id: AttemptId
    attempt: Attempt
    parent_terminal: AttemptTerminal
    exposure_evidence_refs: tuple[ArtifactRef, ...]
    isolation_evidence_refs: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        if self.assignment_id != self.parent_assignment_id:
            raise InvalidAttemptTerminalError(
                "retry assignment must remain linked to the parent assignment"
            )
        if self.attempt.run_id != self.run_id:
            raise InvalidAttemptTerminalError("retry attempt must remain linked to the parent run")
        if self.parent_terminal.attempt_id != self.parent_attempt_id:
            raise InvalidAttemptTerminalError("retry parent terminal must match parent attempt")
        if self.parent_terminal.run_id != self.run_id:
            raise InvalidAttemptTerminalError("retry parent terminal must remain linked to the run")
        object.__setattr__(self, "exposure_evidence_refs", tuple(self.exposure_evidence_refs))
        object.__setattr__(self, "isolation_evidence_refs", tuple(self.isolation_evidence_refs))


@dataclass(frozen=True, slots=True)
class FreshIsolationAttestation:
    """Evidence-backed claim that a retry will receive fresh isolation."""

    attested: bool
    evidence_refs: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))

    @property
    def is_conclusive(self) -> bool:
        return self.attested and bool(self.evidence_refs)


@dataclass(frozen=True, slots=True)
class InternalRetryPolicy:
    """Frozen, independently bounded retry allowance for one subsystem."""

    subsystem: InternalRetrySubsystem
    maximum_retries: int

    def __post_init__(self) -> None:
        if not isinstance(self.subsystem, InternalRetrySubsystem):
            raise InvalidAttemptTerminalError("internal retry subsystem is invalid")
        if self.maximum_retries < 0:
            raise InvalidAttemptTerminalError("internal retry maximum must not be negative")


@dataclass(frozen=True, slots=True)
class InternalRetryRecord:
    attempt_id: AttemptId
    subsystem: InternalRetrySubsystem
    retry_number: int

    def __post_init__(self) -> None:
        if self.retry_number < 1:
            raise InvalidAttemptTerminalError("internal retry number must start at one")


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


@dataclass(frozen=True, slots=True)
class ProductIdentityChain:
    """Opaque treatment-stratum identities carried across product evidence layers."""

    stratum: EvaluationStratum
    stratum_id: StratumId
    protocol_id: ProtocolId
    assignment_id: AssignmentId
    endpoint_id: EndpointId
    cost_entry_id: CostEntryId
    analysis_id: AnalysisId
    report_id: ReportId
    claim_id: ClaimId

    def __post_init__(self) -> None:
        if self.stratum not in {EvaluationStratum.PRODUCT, EvaluationStratum.DIRECT_ENGINE}:
            raise InvalidArtifactManifestError(
                "product identity chain requires a product or engine treatment stratum"
            )

    def to_record(self) -> dict[str, str]:
        return {
            "stratum": self.stratum.value,
            "stratum_id": str(self.stratum_id),
            "protocol_id": str(self.protocol_id),
            "assignment_id": str(self.assignment_id),
            "endpoint_id": str(self.endpoint_id),
            "cost_entry_id": str(self.cost_entry_id),
            "analysis_id": str(self.analysis_id),
            "report_id": str(self.report_id),
            "claim_id": str(self.claim_id),
        }
