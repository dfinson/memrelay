"""Typed domain failures."""


class DomainError(ValueError):
    """Base class for domain validation failures."""


class InvalidIdentifierError(DomainError):
    """An opaque identifier is malformed or contains prohibited information."""


class InvalidLifecycleTransitionError(DomainError):
    """A requested run lifecycle transition is not part of the frozen graph."""


class InvalidAttemptTerminalError(DomainError):
    """An attempt terminal classification is outside the frozen vocabulary."""


class InvalidArtifactManifestError(DomainError):
    """An artifact manifest violates the versioned domain contract."""


class ArtifactIntegrityError(DomainError):
    """Artifact bytes do not match their immutable content address."""


class IneligibleEvidenceError(DomainError):
    """Evidence from a non-conformant adapter cannot support inclusion."""


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
