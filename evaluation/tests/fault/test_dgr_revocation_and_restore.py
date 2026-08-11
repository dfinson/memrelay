from __future__ import annotations

from datetime import timedelta

import pytest
from memrelay_eval.domain.errors import CrossRepositoryDeniedError, StageControlError
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
    RunId,
)
from memrelay_eval.evidence.governance import DgrProofStatus, DgrRepositoryAuthorization
from memrelay_eval.orchestration.control import CrossRepositoryAdmissionController
from memrelay_eval.orchestration.limits import (
    CircuitBreakerAdmissionController,
    CircuitBreakerReason,
    FrozenLimitEnvelope,
)
from tests.contract.test_dgr_qualification import _NOW, make_bundle


def test_active_revocation_invalidates_the_entire_dgr_bundle_without_fallback() -> None:
    bundle = make_bundle(status=DgrProofStatus.REVOKED)

    assert bundle.is_current(_NOW) is False
    assert bundle.is_current(_NOW + timedelta(seconds=1)) is False


def test_bundle_rejects_partial_aggregate_or_missing_control() -> None:
    bundle = make_bundle()

    with pytest.raises(StageControlError, match="controls incomplete"):
        type(bundle)(
            principal_id=bundle.principal_id,
            authorization_id=bundle.authorization_id,
            authorization_version=bundle.authorization_version,
            purpose_id=bundle.purpose_id,
            purpose_version=bundle.purpose_version,
            policy_version=bundle.policy_version,
            revocation_generation=bundle.revocation_generation,
            proofs=bundle.proofs[:-1],
        )


def test_revoked_dgr_trips_breaker_stops_work_and_never_selects_fallback() -> None:
    bundle = make_bundle(status=DgrProofStatus.REVOKED)
    repository_id = bundle.repository_ids[0]
    run_id = RunId.new()
    breaker = CircuitBreakerAdmissionController(
        stage_id="cross-repo-stage",
        stage_envelope=FrozenLimitEnvelope("stage", "a" * 64, {"copilot_tokens": 1}),
        run_envelopes={run_id: FrozenLimitEnvelope("run", "b" * 64, {"copilot_tokens": 1})},
    )
    request = RepositoryAccessRequest(
        request_id=GovernanceRequestId.new(),
        task_repository_id=repository_id,
        requested_repository_id=repository_id,
        principal_id=PrincipalId(bundle.principal_id),
        authorization_id=AuthorizationId(bundle.authorization_id),
        authorization_version=AuthorizationVersionId(bundle.authorization_version),
        purpose_id=PurposeId(bundle.purpose_id),
        purpose_version=PurposeVersionId(bundle.purpose_version),
        policy_version=PolicyVersionId(bundle.policy_version),
        valid_from=_NOW - timedelta(minutes=1),
        valid_until=_NOW + timedelta(minutes=1),
        revocation_state=RevocationState.ACTIVE,
        stage=EvaluationStage.CROSS_REPOSITORY,
    )
    started: list[str] = []

    with pytest.raises(CrossRepositoryDeniedError):
        CrossRepositoryAdmissionController(
            authority=DgrRepositoryAuthorization(bundle), circuit_breaker=breaker
        ).start_repository_operation(request, _NOW, lambda: started.append("started"))

    assert started == []
    assert breaker.records[-1].reason is CircuitBreakerReason.GOVERNANCE_REVOKED
