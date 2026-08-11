"""Typed domain failures."""


class DomainError(ValueError):
    """Base class for domain validation failures."""


class ConfigurationError(DomainError):
    """An explicit evaluator configuration is invalid without exposing its values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class UnknownConfigurationKeyError(ConfigurationError):
    """A configuration source supplied a key outside the versioned contract."""

    def __init__(self) -> None:
        super().__init__("unknown_configuration_key")


class AmbiguousConfigurationKeyError(ConfigurationError):
    """A configuration source supplied an ambiguous key representation."""

    def __init__(self) -> None:
        super().__init__("ambiguous_configuration_key")


class InvalidConfigurationError(ConfigurationError):
    """A recognized non-secret configuration value has an invalid shape."""

    def __init__(self) -> None:
        super().__init__("invalid_configuration")


class SecretConfigurationError(ConfigurationError):
    """A secret value was supplied where only a credential reference is allowed."""

    def __init__(self) -> None:
        super().__init__("secret_value_in_ordinary_configuration")


class FrozenInputMutationError(DomainError):
    """An assigned enrollment plan differs from its sealed input set."""

    def __init__(self, field: str) -> None:
        self.code = "frozen_input_mutation"
        self.field = field
        super().__init__(f"frozen enrollment input changed: {field}")


class EnrollmentLineageError(DomainError):
    """A replacement plan lacks the immutable lineage required for its change."""

    code = "invalid_enrollment_plan_lineage"


class EnvironmentStratumChangedError(DomainError):
    """A host fingerprint no longer matches the frozen assignment stratum."""

    code = "environment_stratum_changed"


class AgentParityMismatchError(DomainError):
    """A paired attempt differs outside its declared intervention delta."""

    def __init__(self, code: str, fields: tuple[str, ...]) -> None:
        self.code = code
        self.fields = fields
        super().__init__(code.replace("_", " "))


class ProtocolDeltaAuthorityError(DomainError):
    """A sealed protocol does not contain a usable opaque delta commitment."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class IneligibleEnrollmentError(DomainError):
    """A Story 1.4 disposition does not permit enrollment."""

    code = "enrollment_disposition_not_eligible"


class CatalogEligibilityBindingError(DomainError):
    """An enrollment disposition is not the exact eligible record in its catalog."""

    code = "catalog_eligibility_binding_invalid"


class InvalidIdentifierError(DomainError):
    """An opaque identifier is malformed or contains prohibited information."""


class InvalidLifecycleTransitionError(DomainError):
    """A requested run lifecycle transition is not part of the frozen graph."""


class InvalidAttemptTerminalError(DomainError):
    """An attempt terminal classification is outside the frozen vocabulary."""


class AttemptTerminalAlreadyRecordedError(DomainError):
    """An attempt may receive only one immutable terminal record."""

    code = "attempt_terminal_already_recorded"


class AttemptExecutionClaimDeniedError(DomainError):
    """An attempt is already terminal or owned by another execution claimant."""

    code = "attempt_execution_claim_denied"


class RetryDeniedError(DomainError):
    """A retry request violates the frozen retry policy."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class InternalRetryLimitExceededError(DomainError):
    """A subsystem exhausted its separately bounded internal retry allowance."""

    code = "internal_retry_limit_exceeded"


class InvalidArtifactManifestError(DomainError):
    """An artifact manifest violates the versioned domain contract."""


class ArtifactIntegrityError(DomainError):
    """Artifact bytes do not match their immutable content address."""


class ArtifactAuthorityConflictError(ArtifactIntegrityError):
    """Two immutable records claim incompatible authority for one artifact."""


class ArtifactRetentionError(DomainError):
    """Immutable evidence cannot be removed before linked claim retirement."""


class IneligibleEvidenceError(DomainError):
    """Evidence from a non-conformant adapter cannot support inclusion."""


class InvalidGovernanceRequestError(DomainError):
    """A repository authorization request does not satisfy the frozen contract."""


class CrossRepositoryDeniedError(DomainError):
    """Repository access was denied before any repository operation."""

    def __init__(self, reason: object) -> None:
        self.reason = reason
        super().__init__(f"cross-repository execution denied: {reason}")


class ConformancePauseError(DomainError):
    """A locked execution substrate drifted and must not be substituted."""

    def __init__(self, code: str, message: str, evidence: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.evidence = evidence


class BackupConformanceError(ConformancePauseError):
    """Backup or restore proof failed; paid-pilot admission remains blocked."""

    def __init__(self, code: str, evidence: tuple[str, ...] = ()) -> None:
        super().__init__(code, "backup or restore conformance failed", evidence)


class QualificationLimitError(ConformancePauseError):
    """A finite nonstudy qualification envelope cannot authorize another session."""


class RuntimeLockError(ConformancePauseError):
    """The pinned SDK wheel or bundled runtime cannot be used safely."""


class LedgerIntentConflictError(DomainError):
    """An opaque intent identity was reused with a different payload."""


class LedgerDirectWriteError(DomainError):
    """A caller attempted to bypass the control-owned intent boundary."""


class LedgerOwnershipError(DomainError):
    """A second control repository attempted to own the same ledger database."""


class ProcessEnvironmentError(DomainError):
    """A process environment crossed an undeclared credential boundary."""


class ProcessBoundaryConformanceError(DomainError):
    """Synthetic canaries produced a non-conformant process-boundary projection."""

    def __init__(self, evidence: tuple[object, ...]) -> None:
        self.evidence = evidence
        super().__init__("process boundary conformance failed")


class ProcessLaunchError(DomainError):
    """A disposable process could not be started from its declared launch request."""


class ProcessReuseError(ProcessLaunchError):
    """A role/attempt pair was already consumed and must never be reused."""


class ProcessLimitError(ProcessLaunchError):
    """A bounded local process pool cannot admit another active attempt."""


class ProcessWorkerBoundaryError(ProcessLaunchError):
    """A process request conflicts with its authoritative attempt workspace boundary."""


class InvalidAssignmentAlgorithmError(DomainError):
    """A frozen assignment algorithm record is absent, malformed, or unregistered."""

    code = "invalid_assignment_algorithm"


class SeedCommitmentMismatchError(DomainError):
    """The provisioning seed does not match the sealed commitment."""

    code = "seed_commitment_mismatch"


class AssignmentResolutionDeniedError(DomainError):
    """Assignment resolution was attempted outside the provisioning authority."""

    code = "assignment_resolution_denied"


class ExposureAlreadyRecordedError(DomainError):
    """An attempt has an immutable, append-only exposure record already."""

    code = "exposure_already_recorded"


class DurableConformanceRequiredError(DomainError):
    """A durable execution gate lacks qualified ledger and telemetry adapters."""

    code = "durable_conformance_required"


class ExecutionEvidenceConflictError(DomainError):
    """Required native execution records disagree or are incomplete."""

    code = "execution_evidence_conflict"


class ReconciliationError(DomainError):
    """A required-evidence reconciliation cannot safely reach a terminal decision."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class MaterializationError(DomainError):
    """A reconciled analysis dataset cannot be safely materialized or verified."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class SafetyAnalysisError(DomainError):
    """Safety evidence, frozen policy, or categorical gate input is invalid."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class AnalysisError(DomainError):
    """A frozen analysis request or derivation cannot satisfy its contract."""

    def __init__(self, code: str, fields: tuple[str, ...] = ()) -> None:
        self.code = code
        self.fields = fields
        super().__init__(code.replace("_", " "))


class TerminalDecisionConflictError(ReconciliationError):
    """A later report attempted to replace an immutable inclusion decision."""

    def __init__(
        self,
        code: str = "terminal_inclusion_decision_conflict",
        *,
        report_ref: object | None = None,
        report_manifest_ref: object | None = None,
    ) -> None:
        super().__init__(code)
        self.report_ref = report_ref
        self.report_manifest_ref = report_manifest_ref


class SecretBoundaryViolationError(DomainError):
    """A scan found credential material without retaining or rendering its value."""

    code = "secret_boundary_violation"

    def __init__(
        self,
        findings: tuple[object, ...],
        evidence_refs: tuple[object, ...] = (),
    ) -> None:
        self.findings = findings
        self.evidence_refs = evidence_refs
        super().__init__(self.code)


class UnqualifiedEvidencePortError(IneligibleEvidenceError):
    """Only deterministic unpaid ports may carry Story 2 evidence."""

    code = "unqualified_evidence_port"


class BlindingConformanceError(IneligibleEvidenceError):
    """A blinded evidence transform or preregistered leakage gate failed closed."""

    def __init__(self, code: str, categories: tuple[str, ...] = ()) -> None:
        self.code = code
        self.categories = categories
        super().__init__(code.replace("_", " "))


class JudgePanelConformanceError(IneligibleEvidenceError):
    """A frozen blinded-judge panel contract failed without a substitute path."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class AdjudicationConformanceError(IneligibleEvidenceError):
    """Frozen disagreement adjudication failed without a replacement or retry path."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class TelemetryConformanceError(DomainError):
    """A versioned telemetry contract, delivery, or Collector proof failed closed."""

    def __init__(self, code: str, fields: tuple[str, ...] = ()) -> None:
        self.code = code
        self.fields = fields
        super().__init__(code.replace("_", " "))


class AuthorityConflictError(DomainError):
    """Provider, credential, resource, or ledger authority is unknown or incompatible."""

    def __init__(self, code: str = "authority_conflict", fields: tuple[str, ...] = ()) -> None:
        self.code = code
        self.fields = fields
        super().__init__(code)


class ExecutionAdapterError(DomainError):
    """The Inspect-to-SDK adapter returned a typed terminal failure."""

    def __init__(self, code: str, message: str, evidence: tuple[object, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.evidence = evidence


class DirectEngineBoundaryError(DomainError):
    """The direct-engine treatment crossed its isolated public boundary."""

    def __init__(self, code: str, evidence: tuple[object, ...] = ()) -> None:
        self.code = code
        self.evidence = evidence
        super().__init__(code.replace("_", " "))


class StratumPoolingError(DomainError):
    """An operation attempted to combine independently governed strata."""

    code = "explicit_stratified_operation_required"


class DynamicHistoryViolationError(DomainError):
    """A sequence attempted to cross a frozen dynamic-history boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class UnsupportedArmError(DynamicHistoryViolationError):
    """The frozen protocol does not define the assigned provisionable arm."""

    def __init__(self) -> None:
        super().__init__("unsupported_arm_before_exposure")


class AnalysisBoundaryError(DynamicHistoryViolationError):
    """An analysis requested ordinary pooling across distinct estimand identities."""

    def __init__(self) -> None:
        super().__init__("sequence_analysis_pooling_forbidden")


class ControlledHistoryViolationError(DomainError):
    """A controlled-history operation attempted to cross a frozen immutability boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class ControlledHistoryMutationError(ControlledHistoryViolationError):
    """A golden checkpoint was rebuilt with different bytes after protocol freeze."""

    def __init__(self) -> None:
        super().__init__("controlled_history_mutation_after_freeze")


class ControlledRestoreMismatchError(ControlledHistoryViolationError):
    """A restore did not produce byte-identical content for its frozen bundle."""


class ControlledEstimandPoolingError(ControlledHistoryViolationError):
    """An analysis requested pooling across controlled/dynamic regimes or strata."""

    def __init__(self) -> None:
        super().__init__("controlled_estimand_pooling_forbidden")


class SnapshotIntegrityError(ArtifactIntegrityError):
    """A supposedly frozen workspace snapshot is incomplete, corrupt, or mutable."""

    code = "workspace_snapshot_integrity_failure"


class SnapshotHardlinkError(SnapshotIntegrityError):
    """A snapshot input has more than one name and cannot establish isolated authority."""

    code = "workspace_snapshot_hardlink_forbidden"


class SnapshotMutationError(SnapshotIntegrityError):
    """A snapshot path changed while its bytes were being captured."""

    code = "workspace_snapshot_toctou_detected"


class GraderContractError(DomainError):
    """A deterministic-grader contract is malformed or no longer matches its pins."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class GraderExecutionError(DomainError):
    """A credential-free grader could not produce a complete executable outcome."""

    def __init__(self, code: str, evidence: tuple[object, ...] = ()) -> None:
        self.code = code
        self.evidence = evidence
        super().__init__(code.replace("_", " "))


class GraderReplayMismatchError(GraderExecutionError):
    """Repeated grading under an identical frozen contract produced different outcomes."""

    def __init__(self) -> None:
        super().__init__("grader_replay_mismatch")


class MalformedGraderOutputError(GraderExecutionError):
    """The grader did not emit exactly one complete frozen result document."""

    def __init__(self) -> None:
        super().__init__("grader_malformed_output")


class NetworkSandboxUnavailableError(GraderExecutionError):
    """The host cannot prove OS-level network denial for a grader process."""

    def __init__(self) -> None:
        super().__init__("grader_network_sandbox_unavailable")
