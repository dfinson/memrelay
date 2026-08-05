"""Fail-closed repository authorization values and policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .errors import InvalidGovernanceRequestError
from .ids import (
    AuthorizationId,
    AuthorizationVersionId,
    GovernanceRequestId,
    PolicyVersionId,
    PrincipalId,
    PurposeId,
    PurposeVersionId,
    RepositoryId,
)


class EvaluationStage(StrEnum):
    """Stage identity relevant to the v1 cross-repository prohibition."""

    ORDINARY = "ordinary"
    CROSS_REPOSITORY = "cross-repo"


class AuthorizationDecision(StrEnum):
    PERMITTED = "permitted"
    DENIED = "denied"


class RevocationState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class GovernanceDenialReason(StrEnum):
    CROSS_REPOSITORY_STAGE_DISABLED = "cross_repository_stage_disabled"
    REPOSITORY_MISMATCH = "repository_mismatch"
    AUTHORIZATION_NOT_CURRENT = "authorization_not_current"
    AUTHORIZATION_REVOKED = "authorization_revoked"


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidGovernanceRequestError(f"{field_name} must be timezone-aware")
    if value.astimezone(UTC) != value:
        raise InvalidGovernanceRequestError(f"{field_name} must be UTC")


@dataclass(frozen=True, slots=True)
class RepositoryAccessRequest:
    """Authorization data, distinct from namespace, remote, cache, and assignment."""

    request_id: GovernanceRequestId
    task_repository_id: RepositoryId
    requested_repository_id: RepositoryId
    principal_id: PrincipalId
    authorization_id: AuthorizationId
    authorization_version: AuthorizationVersionId
    purpose_id: PurposeId
    purpose_version: PurposeVersionId
    policy_version: PolicyVersionId
    valid_from: datetime
    valid_until: datetime
    revocation_state: RevocationState
    stage: EvaluationStage

    def __post_init__(self) -> None:
        _require_utc(self.valid_from, "valid_from")
        _require_utc(self.valid_until, "valid_until")
        if self.valid_until <= self.valid_from:
            raise InvalidGovernanceRequestError("valid_until must be after valid_from")


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    """A typed result that carries no repository identity."""

    decision: AuthorizationDecision
    policy_version: PolicyVersionId
    reason: GovernanceDenialReason | None = None

    def __post_init__(self) -> None:
        if self.decision is AuthorizationDecision.DENIED and self.reason is None:
            raise InvalidGovernanceRequestError("denials require a typed reason")
        if self.decision is AuthorizationDecision.PERMITTED and self.reason is not None:
            raise InvalidGovernanceRequestError("permissions must not carry a denial reason")


@dataclass(frozen=True, slots=True)
class DenialEvidence:
    """The only recordable denial payload."""

    request_id: GovernanceRequestId
    decision: AuthorizationDecision
    policy_version: PolicyVersionId
    reason: GovernanceDenialReason

    def __post_init__(self) -> None:
        if self.decision is not AuthorizationDecision.DENIED:
            raise InvalidGovernanceRequestError("denial evidence requires a denied decision")

    @classmethod
    def from_result(
        cls, request: RepositoryAccessRequest, result: AuthorizationResult
    ) -> DenialEvidence:
        if result.decision is not AuthorizationDecision.DENIED or result.reason is None:
            raise InvalidGovernanceRequestError("only denials can produce denial evidence")
        return cls(request.request_id, result.decision, result.policy_version, result.reason)

    def to_dict(self) -> dict[str, str]:
        return {
            "request_id": str(self.request_id),
            "decision": self.decision.value,
            "policy_version": str(self.policy_version),
            "reason": self.reason.value,
        }


class DenyByDefaultRepositoryAuthorization:
    """The v1 policy, which has no qualification or operator override path."""

    def authorize(self, request: RepositoryAccessRequest, now: datetime) -> AuthorizationResult:
        _require_utc(now, "now")
        if request.stage is EvaluationStage.CROSS_REPOSITORY:
            return AuthorizationResult(
                AuthorizationDecision.DENIED,
                request.policy_version,
                GovernanceDenialReason.CROSS_REPOSITORY_STAGE_DISABLED,
            )
        if request.requested_repository_id != request.task_repository_id:
            return AuthorizationResult(
                AuthorizationDecision.DENIED,
                request.policy_version,
                GovernanceDenialReason.REPOSITORY_MISMATCH,
            )
        if request.revocation_state is RevocationState.REVOKED:
            return AuthorizationResult(
                AuthorizationDecision.DENIED,
                request.policy_version,
                GovernanceDenialReason.AUTHORIZATION_REVOKED,
            )
        if now < request.valid_from or now >= request.valid_until:
            return AuthorizationResult(
                AuthorizationDecision.DENIED,
                request.policy_version,
                GovernanceDenialReason.AUTHORIZATION_NOT_CURRENT,
            )
        return AuthorizationResult(AuthorizationDecision.PERMITTED, request.policy_version)
