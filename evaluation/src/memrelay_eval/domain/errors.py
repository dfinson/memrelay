"""Typed domain failures."""


class DomainError(ValueError):
    """Base class for domain validation failures."""


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
