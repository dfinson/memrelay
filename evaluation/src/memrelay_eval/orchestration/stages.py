"""Stage-entry refusal routes for evaluator v1."""

from __future__ import annotations

from datetime import UTC, datetime

from memrelay_eval.domain.governance import (
    EvaluationStage,
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


def refuse_cross_repository_stage() -> None:
    """Refuse the unavailable v1 stage before any repository identity is resolved."""

    now = datetime.now(UTC)
    repository_id = RepositoryId.new()
    request = RepositoryAccessRequest(
        request_id=GovernanceRequestId.new(),
        task_repository_id=repository_id,
        requested_repository_id=repository_id,
        principal_id=PrincipalId.new(),
        authorization_id=AuthorizationId.new(),
        authorization_version=AuthorizationVersionId.new(),
        purpose_id=PurposeId.new(),
        purpose_version=PurposeVersionId.new(),
        policy_version=PolicyVersionId.new(),
        valid_from=now,
        valid_until=now.replace(year=now.year + 1),
        revocation_state=RevocationState.ACTIVE,
        stage=EvaluationStage.CROSS_REPOSITORY,
    )
    CrossRepositoryAdmissionController().authorize_at_entry(request, now)
