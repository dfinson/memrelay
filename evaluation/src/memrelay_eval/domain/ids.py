"""Immutable, opaque domain identifiers."""

from __future__ import annotations

import re
import secrets
from typing import ClassVar

from .errors import InvalidIdentifierError

_OPAQUE_VALUE = re.compile(r"^[a-f0-9]{32}$")
_PROHIBITED_LABELS = ("arm", "treatment")


class OpaqueId(str):
    """A typed opaque identifier with a fixed, non-semantic prefix."""

    prefix: ClassVar[str] = "id"

    def __new__(cls, value: str) -> OpaqueId:
        expected_prefix = f"{cls.prefix}_"
        if not isinstance(value, str) or not value.startswith(expected_prefix):
            raise InvalidIdentifierError(f"{cls.__name__} must use prefix {expected_prefix!r}")
        opaque_value = value.removeprefix(expected_prefix)
        if not _OPAQUE_VALUE.fullmatch(opaque_value):
            raise InvalidIdentifierError(
                f"{cls.__name__} must contain exactly 32 lowercase hex characters"
            )
        if any(label in value.lower() for label in _PROHIBITED_LABELS):
            raise InvalidIdentifierError("opaque IDs must not contain treatment labels")
        return str.__new__(cls, value)

    @classmethod
    def new(cls) -> OpaqueId:
        return cls(f"{cls.prefix}_{secrets.token_hex(16)}")

    @classmethod
    def from_digest(cls, sha256: str) -> OpaqueId:
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise InvalidIdentifierError("digest-backed IDs require a lowercase SHA-256 digest")
        return cls(f"{cls.prefix}_{sha256[:32]}")


class ExperimentId(OpaqueId):
    prefix = "exp"


class ProtocolId(OpaqueId):
    prefix = "protocol"


class ScenarioId(OpaqueId):
    prefix = "scenario"


class TaskId(OpaqueId):
    prefix = "task"


class HistoryId(OpaqueId):
    prefix = "history"


class SequenceId(OpaqueId):
    """Opaque identity for the unit assigned in a dynamic-history protocol."""

    prefix = "sequence"


class EpisodeId(OpaqueId):
    """Opaque identity for one ordered member of a dynamic sequence."""

    prefix = "episode"


class AssignmentId(OpaqueId):
    prefix = "assignment"


class RunId(OpaqueId):
    prefix = "run"


class AttemptId(OpaqueId):
    prefix = "attempt"


class ArtifactId(OpaqueId):
    prefix = "art"


class ConfigurationId(OpaqueId):
    prefix = "config"


class EnrollmentPlanId(OpaqueId):
    prefix = "enrollment"


class EnvironmentStratumId(OpaqueId):
    prefix = "environment"


class EvidenceId(OpaqueId):
    prefix = "evidence"


class EndpointId(OpaqueId):
    prefix = "endpoint"


class ClaimId(OpaqueId):
    prefix = "claim"


class CostEntryId(OpaqueId):
    prefix = "cost"


class RuntimeId(OpaqueId):
    prefix = "runtime"


class AnalysisId(OpaqueId):
    prefix = "analysis"


class ReportId(OpaqueId):
    prefix = "report"


class StratumId(OpaqueId):
    prefix = "stratum"


class InclusionId(OpaqueId):
    prefix = "inclusion"


class RetentionPolicyId(OpaqueId):
    prefix = "ret"


class RepositoryId(OpaqueId):
    """Opaque repository authorization identity, never a remote or namespace."""

    prefix = "repository"


class PrincipalId(OpaqueId):
    """Opaque authenticated-principal identity."""

    prefix = "principal"


class AuthorizationId(OpaqueId):
    """Opaque identity of an authorization grant."""

    prefix = "authorization"


class AuthorizationVersionId(OpaqueId):
    """Opaque version of an authorization grant."""

    prefix = "authorizationversion"


class PurposeId(OpaqueId):
    """Opaque identity of the authorized purpose."""

    prefix = "purpose"


class PurposeVersionId(OpaqueId):
    """Opaque version of the authorized purpose."""

    prefix = "purposeversion"


class GovernanceRequestId(OpaqueId):
    """Opaque identity used in privacy-minimized denial evidence."""

    prefix = "govrequest"


class PolicyVersionId(OpaqueId):
    """Opaque version or hash identity for the governing policy."""

    prefix = "policy"


class IntentId(OpaqueId):
    """Opaque delivery identity for a worker-to-control ledger intent."""

    prefix = "intent"
