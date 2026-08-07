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
