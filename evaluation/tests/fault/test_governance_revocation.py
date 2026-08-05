from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from memrelay_eval.domain.errors import CrossRepositoryDeniedError
from memrelay_eval.domain.governance import (
    AuthorizationDecision,
    AuthorizationResult,
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
from memrelay_eval.orchestration.control import CrossRepositoryAdmissionController


class RevocableAuthority:
    def __init__(self) -> None:
        self.revoked = False
        self.calls = 0

    def authorize(self, request: RepositoryAccessRequest, now: datetime) -> AuthorizationResult:
        del now
        self.calls += 1
        if self.revoked:
            return AuthorizationResult(
                AuthorizationDecision.DENIED,
                request.policy_version,
                GovernanceDenialReason.AUTHORIZATION_REVOKED,
            )
        return AuthorizationResult(AuthorizationDecision.PERMITTED, request.policy_version)


def ordinary_request() -> RepositoryAccessRequest:
    now = datetime(2026, 8, 5, tzinfo=UTC)
    repository_id = RepositoryId.new()
    return RepositoryAccessRequest(
        request_id=GovernanceRequestId.new(),
        task_repository_id=repository_id,
        requested_repository_id=repository_id,
        principal_id=PrincipalId.new(),
        authorization_id=AuthorizationId.new(),
        authorization_version=AuthorizationVersionId.new(),
        purpose_id=PurposeId.new(),
        purpose_version=PurposeVersionId.new(),
        policy_version=PolicyVersionId.new(),
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(minutes=1),
        revocation_state=RevocationState.ACTIVE,
        stage=EvaluationStage.ORDINARY,
    )


def test_revocation_between_entry_and_start_prevents_new_repository_operation() -> None:
    authority = RevocableAuthority()
    controller = CrossRepositoryAdmissionController(authority=authority)
    operations_started: list[str] = []
    request = ordinary_request()

    controller.authorize_at_entry(request, datetime(2026, 8, 5, tzinfo=UTC))
    authority.revoked = True

    with pytest.raises(CrossRepositoryDeniedError) as failure:
        controller.start_repository_operation(
            request,
            datetime(2026, 8, 5, tzinfo=UTC),
            lambda: operations_started.append("started"),
        )

    assert failure.value.reason is GovernanceDenialReason.AUTHORIZATION_REVOKED
    assert operations_started == []
    assert authority.calls == 2
