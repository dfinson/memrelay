from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from memrelay_eval.domain.governance import (
    AuthorizationDecision,
    DenialEvidence,
    DenyByDefaultRepositoryAuthorization,
    EvaluationStage,
    GovernanceDenialReason,
    RepositoryAccessRequest,
    RevocationState,
)
from memrelay_eval.domain.ids import (
    AuthorizationId,
    AuthorizationVersionId,
    GovernanceRequestId,
    PolicyVersionId,
    PrincipalId,
    PurposeId,
    PurposeVersionId,
    RepositoryId,
)


def request(
    *,
    requested_repository_id: RepositoryId | None = None,
    stage: EvaluationStage = EvaluationStage.ORDINARY,
    revocation_state: RevocationState = RevocationState.ACTIVE,
    valid_until: datetime | None = None,
) -> RepositoryAccessRequest:
    now = datetime(2026, 8, 5, tzinfo=UTC)
    task_repository_id = RepositoryId.new()
    return RepositoryAccessRequest(
        request_id=GovernanceRequestId.new(),
        task_repository_id=task_repository_id,
        requested_repository_id=requested_repository_id or task_repository_id,
        principal_id=PrincipalId.new(),
        authorization_id=AuthorizationId.new(),
        authorization_version=AuthorizationVersionId.new(),
        purpose_id=PurposeId.new(),
        purpose_version=PurposeVersionId.new(),
        policy_version=PolicyVersionId.new(),
        valid_from=now - timedelta(minutes=1),
        valid_until=valid_until or now + timedelta(minutes=1),
        revocation_state=revocation_state,
        stage=stage,
    )


def test_repository_identity_is_opaque_and_separate_from_authorization_identity() -> None:
    access_request = request()

    assert access_request.task_repository_id == access_request.requested_repository_id
    assert access_request.authorization_id != access_request.task_repository_id
    assert access_request.authorization_version != access_request.purpose_version
    assert access_request.principal_id != access_request.purpose_id
    with pytest.raises(FrozenInstanceError):
        access_request.stage = EvaluationStage.CROSS_REPOSITORY  # type: ignore[misc]


@pytest.mark.parametrize(
    "representation",
    ("same-owner", "remote-alias", "fork", "case-variant", "path-variant", "stale-cache"),
)
def test_any_distinct_repository_identity_is_denied_without_normalization(
    representation: str,
) -> None:
    del representation
    policy = DenyByDefaultRepositoryAuthorization()
    access_request = request(requested_repository_id=RepositoryId.new())

    result = policy.authorize(access_request, datetime(2026, 8, 5, tzinfo=UTC))

    assert result.decision is AuthorizationDecision.DENIED
    assert result.reason is GovernanceDenialReason.REPOSITORY_MISMATCH


def test_cross_repository_stage_is_denied_even_for_the_task_repository() -> None:
    policy = DenyByDefaultRepositoryAuthorization()

    result = policy.authorize(
        request(stage=EvaluationStage.CROSS_REPOSITORY),
        datetime(2026, 8, 5, tzinfo=UTC),
    )

    assert result.decision is AuthorizationDecision.DENIED
    assert result.reason is GovernanceDenialReason.CROSS_REPOSITORY_STAGE_DISABLED


@pytest.mark.parametrize(
    ("revocation_state", "valid_until", "reason"),
    (
        (
            RevocationState.REVOKED,
            None,
            GovernanceDenialReason.AUTHORIZATION_REVOKED,
        ),
        (
            RevocationState.ACTIVE,
            datetime(2026, 8, 5, tzinfo=UTC) - timedelta(seconds=1),
            GovernanceDenialReason.AUTHORIZATION_NOT_CURRENT,
        ),
    ),
)
def test_revoked_or_expired_authorization_returns_a_typed_refusal(
    revocation_state: RevocationState,
    valid_until: datetime | None,
    reason: GovernanceDenialReason,
) -> None:
    result = DenyByDefaultRepositoryAuthorization().authorize(
        request(revocation_state=revocation_state, valid_until=valid_until),
        datetime(2026, 8, 5, tzinfo=UTC),
    )

    assert result.decision is AuthorizationDecision.DENIED
    assert result.reason is reason


def test_denial_evidence_has_only_the_privacy_minimized_contract() -> None:
    access_request = request(requested_repository_id=RepositoryId.new())
    result = DenyByDefaultRepositoryAuthorization().authorize(
        access_request, datetime(2026, 8, 5, tzinfo=UTC)
    )
    evidence = DenialEvidence.from_result(access_request, result)

    assert evidence.to_dict() == {
        "request_id": str(access_request.request_id),
        "decision": "denied",
        "policy_version": str(access_request.policy_version),
        "reason": "repository_mismatch",
    }
