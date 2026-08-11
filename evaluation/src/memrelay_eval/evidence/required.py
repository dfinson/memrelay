"""Frozen, conditional required-evidence matrix for terminal reconciliation."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import ExecutionEvidenceConflictError, UnqualifiedEvidencePortError
from memrelay_eval.domain.states import AttemptTerminalKind, EvaluationStratum, HistoryMode

REQUIRED_EVIDENCE_MATRIX_VERSION = "1.0.0"
REQUIRED_EVIDENCE_PRODUCER_POLICY_VERSION = "1.0.0"

# Retained for Story 2.10 execution evidence callers.  Story 4.5 expands this
# native inventory into the terminal evidence matrix below.
REQUIRED_NATIVE_EVIDENCE_KINDS = frozenset(
    {
        "inspect_eval",
        "inspect_json",
        "sdk_events",
        "sdk_terminal",
        "workspace_patch",
        "usage",
        "limits",
        "cancellation",
        "typed_failure",
        "monotonic_active_agent_time",
        "provisioning_time",
        "queue_time",
        "backoff_time",
        "cleanup_time",
    }
)


class EvidenceKind(StrEnum):
    ASSIGNMENT = "assignment"
    LIFECYCLE = "lifecycle"
    INSPECT_EVAL = "inspect_eval"
    INSPECT_JSON = "inspect_json"
    SDK_EVENTS = "sdk_events"
    SDK_TERMINAL = "sdk_terminal"
    MEMRELAY_LOGS = "memrelay_logs"
    TELEMETRY = "telemetry"
    WORKSPACE_BASELINE = "workspace_baseline"
    WORKSPACE_TERMINAL = "workspace_terminal"
    WORKSPACE_PATCH = "workspace_patch"
    TREATMENT = "treatment"
    GRADING = "grading"
    PANEL = "panel"
    CALIBRATION = "calibration"
    ADJUDICATION = "adjudication"
    COST_COPILOT = "cost_copilot"
    COST_FRAMEWORK = "cost_framework"
    COST_LOCAL = "cost_local"
    CONFIGURATION = "configuration"
    MODEL_LOCK = "model_lock"
    PARITY = "parity"
    CLEANUP = "cleanup"
    TRANSITIONS = "transitions"
    REFERENCED_HASHES = "referenced_hashes"


class RequirementMode(StrEnum):
    PRIMARY_REQUIRED = "primary_required"
    CONDITIONALLY_REQUIRED = "conditionally_required"
    EXPLICIT_UNAVAILABLE_PERMITTED = "explicit_unavailable_permitted"
    PROHIBITED = "prohibited"


@dataclass(frozen=True, slots=True)
class ArtifactManifestProducerIdentity:
    """One exact ArtifactManifest producer identity admitted by control policy."""

    component: str
    version: str

    def __post_init__(self) -> None:
        if not self.component or not self.version:
            raise ValueError("artifact_manifest_producer_identity_requires_component_and_version")


_EVALUATOR_PRODUCER_VERSION = "0.1.0"
REQUIRED_EVIDENCE_PRODUCER_POLICY: Mapping[str, frozenset[ArtifactManifestProducerIdentity]] = (
    MappingProxyType(
        {
            "assignment_service": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.assignment", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "ledger": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.ledger", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "inspect": frozenset({ArtifactManifestProducerIdentity("inspect_ai", "0.3.252")}),
            "copilot_sdk": frozenset(
                {ArtifactManifestProducerIdentity("github_copilot_sdk", "1.0.8")}
            ),
            "memrelay": frozenset({ArtifactManifestProducerIdentity("memrelay", "0.1.0")}),
            "otel_transport": frozenset(
                {ArtifactManifestProducerIdentity("opentelemetry_sdk", "1.44.0")}
            ),
            "controlled_history_workspace": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.controlled_history_workspace",
                        _EVALUATOR_PRODUCER_VERSION,
                    )
                }
            ),
            "dynamic_history_workspace": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.dynamic_history_workspace",
                        _EVALUATOR_PRODUCER_VERSION,
                    )
                }
            ),
            "treatment_process": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.treatment_process", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "direct_engine_treatment": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.direct_engine_treatment", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "deterministic_grader": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.deterministic_grader", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "judge_panel": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.judge_panel", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "panel_calibration": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.panel_calibration", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "adjudicator": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.adjudicator", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "copilot_usage_ledger": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.copilot_usage_ledger", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "framework_cost_ledger": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.framework_cost_ledger", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "local_resource_ledger": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.local_resource_ledger", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "configuration": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.configuration", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "model_lock": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.model_lock", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "direct_engine_parity": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.direct_engine_parity", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "product_parity": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.product_parity", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "cleanup": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.cleanup", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
            "cas_manifest": frozenset(
                {
                    ArtifactManifestProducerIdentity(
                        "memrelay_eval.cas_manifest", _EVALUATOR_PRODUCER_VERSION
                    )
                }
            ),
        }
    )
)


@dataclass(frozen=True, slots=True)
class RequiredEvidenceProducerPolicy:
    """A canonical snapshot of the producers admitted by evidence authority."""

    authorities: Mapping[str, frozenset[ArtifactManifestProducerIdentity]]
    schema_version: str = REQUIRED_EVIDENCE_PRODUCER_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REQUIRED_EVIDENCE_PRODUCER_POLICY_VERSION:
            raise ValueError("unsupported_required_evidence_producer_policy_version")
        if not self.authorities or any(
            not authority or not identities for authority, identities in self.authorities.items()
        ):
            raise ValueError("required_evidence_producer_policy_incomplete")
        object.__setattr__(
            self,
            "authorities",
            MappingProxyType(
                {
                    authority: frozenset(identities)
                    for authority, identities in self.authorities.items()
                }
            ),
        )

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "authorities": [
                {
                    "authority": authority,
                    "producers": [
                        {"component": identity.component, "version": identity.version}
                        for identity in sorted(
                            identities, key=lambda item: (item.component, item.version)
                        )
                    ],
                }
                for authority, identities in sorted(self.authorities.items())
            ],
        }

    @property
    def sha256(self) -> str:
        return canonical_digest(self.to_document())

    def is_authoritative(self, authority: str, component: str, version: str) -> bool:
        return ArtifactManifestProducerIdentity(component, version) in self.authorities.get(
            authority, frozenset()
        )


def required_evidence_producer_policy() -> RequiredEvidenceProducerPolicy:
    """Snapshot the versioned authority producer policy for a governed run."""

    return RequiredEvidenceProducerPolicy(REQUIRED_EVIDENCE_PRODUCER_POLICY)


def producer_identity_for_authority(authority: str) -> ArtifactManifestProducerIdentity:
    """Return the sole fixture/default identity admitted for one matrix authority."""

    try:
        identities = required_evidence_producer_policy().authorities[authority]
    except KeyError as error:
        raise ValueError("required_evidence_authority_has_no_producer_policy") from error
    if len(identities) != 1:
        raise ValueError("required_evidence_authority_requires_explicit_producer_selection")
    return next(iter(identities))


def producer_is_authoritative(
    authority: str,
    component: str,
    version: str,
    policy: RequiredEvidenceProducerPolicy | None = None,
) -> bool:
    """Accept only the frozen producer component/version identities for an authority."""

    return (policy or required_evidence_producer_policy()).is_authoritative(
        authority, component, version
    )


_STAGES = frozenset({"conformance", "integration", "pilot", "primary", "secondary", "cross-repo"})
_PROVIDER_PATHS = frozenset({"copilot_task_agent", "direct_engine"})
_PRE_EXPOSURE_TERMINALS = frozenset({AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE})
_GRADING_TERMINALS = frozenset({AttemptTerminalKind.SUCCEEDED, AttemptTerminalKind.GRADER_FAILED})


@dataclass(frozen=True, slots=True)
class EvidenceMatrixKey:
    """All protocol conditions that select a frozen terminal evidence matrix."""

    stage: str
    stratum: EvaluationStratum
    history_mode: HistoryMode
    task_state: str
    failure_state: AttemptTerminalKind
    provider_path: str
    grader_required: bool
    panel_required: bool
    adjudication_required: bool

    def __post_init__(self) -> None:
        if self.stage not in _STAGES:
            raise ValueError("unsupported_reconciliation_stage")
        if self.task_state != "terminal":
            raise ValueError("required_evidence_matrix_requires_terminal_task_state")
        if self.provider_path not in _PROVIDER_PATHS:
            raise ValueError("unsupported_required_evidence_provider_path")
        if not self.grader_required and (self.panel_required or self.adjudication_required):
            raise ValueError("panel_or_adjudication_requires_grader")
        if not self.panel_required and self.adjudication_required:
            raise ValueError("adjudication_requires_panel")

    def to_document(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "stratum": self.stratum.value,
            "history_mode": self.history_mode.value,
            "task_state": self.task_state,
            "failure_state": self.failure_state.value,
            "provider_path": self.provider_path,
            "grader_required": self.grader_required,
            "panel_required": self.panel_required,
            "adjudication_required": self.adjudication_required,
        }


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    kind: EvidenceKind
    mode: RequirementMode
    authorities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.authorities or len(set(self.authorities)) != len(self.authorities):
            raise ValueError("required_evidence_authorities_must_be_unique_and_nonempty")
        if any(
            authority not in REQUIRED_EVIDENCE_PRODUCER_POLICY for authority in self.authorities
        ):
            raise ValueError("required_evidence_authority_has_no_producer_policy")

    def to_document(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "mode": self.mode.value,
            "authorities": list(self.authorities),
        }


@dataclass(frozen=True, slots=True)
class RequiredEvidenceMatrix:
    """A deterministic matrix projection; changing it requires a new version."""

    key: EvidenceMatrixKey
    requirements: Mapping[EvidenceKind, EvidenceRequirement]
    schema_version: str = REQUIRED_EVIDENCE_MATRIX_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REQUIRED_EVIDENCE_MATRIX_VERSION:
            raise ValueError("unsupported_required_evidence_matrix_version")
        if set(self.requirements) != set(EvidenceKind):
            raise ValueError("required_evidence_matrix_is_incomplete")
        object.__setattr__(self, "requirements", MappingProxyType(dict(self.requirements)))

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "key": self.key.to_document(),
            "requirements": [
                self.requirements[kind].to_document() for kind in sorted(EvidenceKind, key=str)
            ],
        }

    @property
    def sha256(self) -> str:
        return canonical_digest(self.to_document())

    @property
    def primary_kinds(self) -> tuple[EvidenceKind, ...]:
        return tuple(
            kind
            for kind in sorted(EvidenceKind, key=str)
            if self.requirements[kind].mode
            in (
                RequirementMode.PRIMARY_REQUIRED,
                RequirementMode.EXPLICIT_UNAVAILABLE_PERMITTED,
            )
        )


def required_evidence_matrix(key: EvidenceMatrixKey) -> RequiredEvidenceMatrix:
    """Select the one frozen, condition-specific matrix for a terminal attempt."""

    treatment_authority = (
        "direct_engine_treatment"
        if key.stratum is EvaluationStratum.DIRECT_ENGINE
        else "treatment_process"
    )
    workspace_authority = (
        "dynamic_history_workspace"
        if key.history_mode is HistoryMode.DYNAMIC
        else "controlled_history_workspace"
    )
    requirements = {
        EvidenceKind.ASSIGNMENT: _primary(EvidenceKind.ASSIGNMENT, "assignment_service"),
        EvidenceKind.LIFECYCLE: _primary(EvidenceKind.LIFECYCLE, "ledger"),
        EvidenceKind.INSPECT_EVAL: _execution_requirement(
            EvidenceKind.INSPECT_EVAL, key, "inspect"
        ),
        EvidenceKind.INSPECT_JSON: _execution_requirement(
            EvidenceKind.INSPECT_JSON, key, "inspect"
        ),
        EvidenceKind.SDK_EVENTS: _provider_requirement(EvidenceKind.SDK_EVENTS, key, "copilot_sdk"),
        EvidenceKind.SDK_TERMINAL: _provider_requirement(
            EvidenceKind.SDK_TERMINAL, key, "copilot_sdk"
        ),
        EvidenceKind.MEMRELAY_LOGS: _primary(EvidenceKind.MEMRELAY_LOGS, "memrelay"),
        EvidenceKind.TELEMETRY: _primary(EvidenceKind.TELEMETRY, "otel_transport"),
        EvidenceKind.WORKSPACE_BASELINE: _baseline_requirement(key, workspace_authority),
        EvidenceKind.WORKSPACE_TERMINAL: _execution_requirement(
            EvidenceKind.WORKSPACE_TERMINAL, key, workspace_authority
        ),
        EvidenceKind.WORKSPACE_PATCH: _execution_requirement(
            EvidenceKind.WORKSPACE_PATCH, key, workspace_authority
        ),
        EvidenceKind.TREATMENT: _execution_requirement(
            EvidenceKind.TREATMENT, key, treatment_authority
        ),
        EvidenceKind.GRADING: _grading_requirement(key),
        EvidenceKind.PANEL: _panel_requirement(key),
        EvidenceKind.CALIBRATION: _calibration_requirement(key),
        EvidenceKind.ADJUDICATION: _adjudication_requirement(key),
        EvidenceKind.COST_COPILOT: _unavailable_permitted(
            EvidenceKind.COST_COPILOT, "copilot_usage_ledger"
        ),
        EvidenceKind.COST_FRAMEWORK: _unavailable_permitted(
            EvidenceKind.COST_FRAMEWORK, "framework_cost_ledger"
        ),
        EvidenceKind.COST_LOCAL: _unavailable_permitted(
            EvidenceKind.COST_LOCAL, "local_resource_ledger"
        ),
        EvidenceKind.CONFIGURATION: _primary(EvidenceKind.CONFIGURATION, "configuration"),
        EvidenceKind.MODEL_LOCK: _primary(EvidenceKind.MODEL_LOCK, "model_lock"),
        EvidenceKind.PARITY: _primary(
            EvidenceKind.PARITY,
            "direct_engine_parity"
            if key.stratum is EvaluationStratum.DIRECT_ENGINE
            else "product_parity",
        ),
        EvidenceKind.CLEANUP: _primary(EvidenceKind.CLEANUP, "cleanup"),
        EvidenceKind.TRANSITIONS: _primary(EvidenceKind.TRANSITIONS, "ledger"),
        EvidenceKind.REFERENCED_HASHES: _primary(EvidenceKind.REFERENCED_HASHES, "cas_manifest"),
    }
    return RequiredEvidenceMatrix(key=key, requirements=requirements)


def all_terminal_matrix_keys() -> Iterator[EvidenceMatrixKey]:
    """Enumerate every frozen terminal condition for matrix contract tests."""

    for stage in sorted(_STAGES):
        for stratum in EvaluationStratum:
            for history_mode in HistoryMode:
                for failure_state in AttemptTerminalKind:
                    for provider_path in sorted(_PROVIDER_PATHS):
                        for grader_required, panel_required, adjudication_required in (
                            (False, False, False),
                            (True, False, False),
                            (True, True, False),
                            (True, True, True),
                        ):
                            yield EvidenceMatrixKey(
                                stage,
                                stratum,
                                history_mode,
                                "terminal",
                                failure_state,
                                provider_path,
                                grader_required,
                                panel_required,
                                adjudication_required,
                            )


def _primary(kind: EvidenceKind, *authorities: str) -> EvidenceRequirement:
    return EvidenceRequirement(kind, RequirementMode.PRIMARY_REQUIRED, tuple(authorities))


def _conditional(kind: EvidenceKind, enabled: bool, *authorities: str) -> EvidenceRequirement:
    return EvidenceRequirement(
        kind,
        RequirementMode.PRIMARY_REQUIRED if enabled else RequirementMode.PROHIBITED,
        tuple(authorities),
    )


def _unavailable_permitted(kind: EvidenceKind, *authorities: str) -> EvidenceRequirement:
    return EvidenceRequirement(
        kind,
        RequirementMode.EXPLICIT_UNAVAILABLE_PERMITTED,
        tuple(authorities),
    )


def _prohibited(kind: EvidenceKind, *authorities: str) -> EvidenceRequirement:
    return EvidenceRequirement(kind, RequirementMode.PROHIBITED, tuple(authorities))


def _execution_requirement(
    kind: EvidenceKind, key: EvidenceMatrixKey, *authorities: str
) -> EvidenceRequirement:
    if key.failure_state in _PRE_EXPOSURE_TERMINALS:
        return _prohibited(kind, *authorities)
    return _primary(kind, *authorities)


def _provider_requirement(
    kind: EvidenceKind, key: EvidenceMatrixKey, *authorities: str
) -> EvidenceRequirement:
    if key.failure_state in _PRE_EXPOSURE_TERMINALS or key.provider_path != "copilot_task_agent":
        return _prohibited(kind, *authorities)
    return _primary(kind, *authorities)


def _baseline_requirement(key: EvidenceMatrixKey, authority: str) -> EvidenceRequirement:
    if key.failure_state in _PRE_EXPOSURE_TERMINALS:
        return _unavailable_permitted(EvidenceKind.WORKSPACE_BASELINE, authority)
    if key.history_mode is HistoryMode.DYNAMIC:
        return _unavailable_permitted(EvidenceKind.WORKSPACE_BASELINE, authority)
    return _primary(EvidenceKind.WORKSPACE_BASELINE, authority)


def _grading_requirement(key: EvidenceMatrixKey) -> EvidenceRequirement:
    if key.failure_state in _PRE_EXPOSURE_TERMINALS or key.failure_state not in _GRADING_TERMINALS:
        return _prohibited(EvidenceKind.GRADING, "deterministic_grader")
    return _conditional(
        EvidenceKind.GRADING,
        key.grader_required or key.failure_state is AttemptTerminalKind.GRADER_FAILED,
        "deterministic_grader",
    )


def _panel_requirement(key: EvidenceMatrixKey) -> EvidenceRequirement:
    return _conditional(
        EvidenceKind.PANEL,
        key.stage != "conformance"
        and key.failure_state is AttemptTerminalKind.SUCCEEDED
        and key.panel_required,
        "judge_panel",
    )


def _calibration_requirement(key: EvidenceMatrixKey) -> EvidenceRequirement:
    return _conditional(
        EvidenceKind.CALIBRATION,
        key.stage != "conformance"
        and key.failure_state is AttemptTerminalKind.SUCCEEDED
        and key.panel_required,
        "panel_calibration",
    )


def _adjudication_requirement(key: EvidenceMatrixKey) -> EvidenceRequirement:
    if (
        key.stage == "conformance"
        or key.failure_state is not AttemptTerminalKind.SUCCEEDED
        or not key.panel_required
    ):
        return _prohibited(EvidenceKind.ADJUDICATION, "adjudicator")
    if key.adjudication_required:
        return _primary(EvidenceKind.ADJUDICATION, "adjudicator")
    return EvidenceRequirement(
        EvidenceKind.ADJUDICATION,
        RequirementMode.CONDITIONALLY_REQUIRED,
        ("adjudicator",),
    )


@dataclass(frozen=True, slots=True)
class NativeEvidenceInventory:
    """All native terminal surfaces are separate hash-addressed artifacts."""

    artifacts: Mapping[str, ArtifactRef]

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", MappingProxyType(dict(self.artifacts)))

    @property
    def missing_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(REQUIRED_NATIVE_EVIDENCE_KINDS.difference(self.artifacts)))

    def require_complete(self) -> None:
        if self.missing_kinds:
            raise ExecutionEvidenceConflictError("native_evidence_incomplete")


def require_unpaid_conformance_ports(*ports: object) -> None:
    """Reject paid or unqualified authority before durable Epic 4 adapters exist."""

    for port in ports:
        if (
            getattr(port, "provenance", None) != "unpaid_conformance"
            or getattr(port, "eligible_for_paid_or_study", None) is not False
        ):
            raise UnqualifiedEvidencePortError(UnqualifiedEvidencePortError.code)
