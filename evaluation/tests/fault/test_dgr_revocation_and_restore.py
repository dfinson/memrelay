from __future__ import annotations

from datetime import timedelta
from threading import Event, Thread

import pytest
from memrelay_eval.domain.errors import (
    BackupConformanceError,
    CrossRepositoryDeniedError,
    StageControlError,
)
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
    RunId,
)
from memrelay_eval.evidence.backup import RestorePolicy, restore_drill
from memrelay_eval.evidence.governance import (
    DgrProofStatus,
    DgrQualificationAuthority,
    DgrRepositoryAuthorization,
    RevocationGenerationAuthority,
)
from memrelay_eval.orchestration.control import CrossRepositoryAdmissionController
from memrelay_eval.orchestration.limits import (
    CircuitBreakerAdmissionController,
    CircuitBreakerReason,
    FrozenLimitEnvelope,
)
from tests.contract.test_dgr_qualification import _NOW, make_bundle
from tests.fault.evidence.test_backup_atomicity import _service


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
            authority=DgrRepositoryAuthorization(
                bundle,
                revocation_authority=RevocationGenerationAuthority(bundle.revocation_generation),
            ),
            circuit_breaker=breaker,
        ).start_repository_operation(request, _NOW, lambda: started.append("started"))

    assert started == []
    assert breaker.records[-1].reason is CircuitBreakerReason.GOVERNANCE_REVOKED


def test_real_restore_drill_qualifies_revoked_content_only_in_quarantine(tmp_path) -> None:
    service, ledger, run, attempt, backup = _service(tmp_path)
    receipt = service.backup_terminal_run(run_id=run, attempt_id=attempt)
    observed: dict[str, object] = {}

    def revoked_restore_probe() -> dict[str, object]:
        if observed:
            return observed
        with pytest.raises(BackupConformanceError) as error:
            restore_drill(
                backup_root=backup,
                generation_id=receipt.generation_id,
                quarantine_root=tmp_path / "revoked-quarantine",
                policy=RestorePolicy("revoked-policy-v2", lambda _: False),
            )
        assert error.value.code == "restore_policy_rejected_artifact"
        observed.update(
            {
                "quarantine_only": True,
                "tombstones_applied": True,
                "authorization_rechecked": True,
                "revocation_rechecked": True,
                "negative_retrieval": not (tmp_path / "revoked-quarantine").exists(),
                "active_index_restore_denied": True,
            }
        )
        return observed

    bundle = DgrQualificationAuthority(
        issuer_id="synthetic-issuer",
        authority_id="restore-drill-authority",
        restore_probe=revoked_restore_probe,
    ).qualify(
        repositories=tuple(
            RepositoryId.from_digest(f"{number:02x}" + "a" * 62) for number in range(24)
        ),
        principal_id="principal_00000000000000000000000000000000",
        authorization_id="authorization_00000000000000000000000000000000",
        authorization_version="authorizationversion_00000000000000000000000000000000",
        purpose_id="purpose_00000000000000000000000000000000",
        purpose_version="purposeversion_00000000000000000000000000000000",
        policy_version="policy_00000000000000000000000000000000",
        revocation_generation=9,
        valid_from=_NOW - timedelta(minutes=1),
        valid_until=_NOW + timedelta(minutes=1),
    )

    assert bundle.is_current(_NOW)
    assert observed["negative_retrieval"] is True
    ledger.close()


def test_live_revocation_generation_changes_after_entry_before_atomic_start() -> None:
    bundle = make_bundle()
    repository_id = bundle.repository_ids[0]
    generation = RevocationGenerationAuthority(bundle.revocation_generation)
    breaker = CircuitBreakerAdmissionController(
        stage_id="cross-repo-stage",
        stage_envelope=FrozenLimitEnvelope("stage", "a" * 64, {"copilot_tokens": 1}),
        run_envelopes={RunId.new(): FrozenLimitEnvelope("run", "b" * 64, {"copilot_tokens": 1})},
    )
    request = RepositoryAccessRequest(
        GovernanceRequestId.new(),
        repository_id,
        repository_id,
        PrincipalId(bundle.principal_id),
        AuthorizationId(bundle.authorization_id),
        AuthorizationVersionId(bundle.authorization_version),
        PurposeId(bundle.purpose_id),
        PurposeVersionId(bundle.purpose_version),
        PolicyVersionId(bundle.policy_version),
        _NOW - timedelta(minutes=1),
        _NOW + timedelta(minutes=1),
        RevocationState.ACTIVE,
        EvaluationStage.CROSS_REPOSITORY,
    )
    reached_start, started = Event(), []

    def revoke_at_barrier() -> None:
        reached_start.set()
        generation.revoke()

    controller = CrossRepositoryAdmissionController(
        authority=DgrRepositoryAuthorization(
            bundle,
            revocation_authority=generation,
            before_atomic_start=revoke_at_barrier,
        ),
        circuit_breaker=breaker,
    )
    controller.authorize_at_entry(request, _NOW)

    with pytest.raises(CrossRepositoryDeniedError):
        controller.start_repository_operation(request, _NOW, lambda: started.append("discovery"))
    assert reached_start.is_set()
    assert started == []
    assert breaker.records[-1].reason is CircuitBreakerReason.GOVERNANCE_REVOKED


def test_live_post_start_revocation_drains_active_work_and_blocks_later_starts() -> None:
    bundle = make_bundle()
    repository_id = bundle.repository_ids[0]
    generation = RevocationGenerationAuthority(bundle.revocation_generation)
    breaker = CircuitBreakerAdmissionController(
        stage_id="cross-repo-stage",
        stage_envelope=FrozenLimitEnvelope("stage", "a" * 64, {"copilot_tokens": 1}),
        run_envelopes={RunId.new(): FrozenLimitEnvelope("run", "b" * 64, {"copilot_tokens": 1})},
    )
    request = RepositoryAccessRequest(
        GovernanceRequestId.new(),
        repository_id,
        repository_id,
        PrincipalId(bundle.principal_id),
        AuthorizationId(bundle.authorization_id),
        AuthorizationVersionId(bundle.authorization_version),
        PurposeId(bundle.purpose_id),
        PurposeVersionId(bundle.purpose_version),
        PolicyVersionId(bundle.policy_version),
        _NOW - timedelta(minutes=1),
        _NOW + timedelta(minutes=1),
        RevocationState.ACTIVE,
        EvaluationStage.CROSS_REPOSITORY,
    )
    entered, release, started = Event(), Event(), []
    controller = CrossRepositoryAdmissionController(
        authority=DgrRepositoryAuthorization(bundle, revocation_authority=generation),
        circuit_breaker=breaker,
    )

    def active_operation() -> None:
        started.append("started")
        entered.set()
        assert release.wait(timeout=5)

    worker = Thread(
        target=lambda: controller.start_repository_operation(request, _NOW, active_operation)
    )
    worker.start()
    assert entered.wait(timeout=5)
    generation.revoke()

    assert breaker.state.value == "draining"
    assert breaker.status_projection()["active_external_operations"] == 1
    release.set()
    worker.join(timeout=5)
    assert worker.is_alive() is False
    with pytest.raises(CrossRepositoryDeniedError):
        controller.start_repository_operation(request, _NOW, lambda: started.append("fallback"))
    assert started == ["started"]
