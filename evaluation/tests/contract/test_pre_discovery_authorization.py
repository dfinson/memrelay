from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from memrelay_eval.cli.main import main
from memrelay_eval.domain.errors import CrossRepositoryDeniedError
from memrelay_eval.domain.governance import (
    AuthorizationDecision,
    AuthorizationResult,
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
from memrelay_eval.orchestration.control import (
    CrossRepositoryAdmissionController,
    InMemoryDenialEvidenceSink,
)


class ForbiddenRepositoryOperations:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def discover(self) -> None:
        self.calls.append("discover")

    def clone(self) -> None:
        self.calls.append("clone")

    def cache_lookup(self) -> None:
        self.calls.append("cache_lookup")

    def assign(self) -> None:
        self.calls.append("assign")

    def acquire_credentials(self) -> None:
        self.calls.append("acquire_credentials")

    def expose_task(self) -> None:
        self.calls.append("expose_task")


def cross_repository_request() -> RepositoryAccessRequest:
    now = datetime(2026, 8, 5, tzinfo=UTC)
    return RepositoryAccessRequest(
        request_id=GovernanceRequestId.new(),
        task_repository_id=RepositoryId.new(),
        requested_repository_id=RepositoryId.new(),
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


def test_denial_happens_before_any_repository_touching_operation() -> None:
    operations = ForbiddenRepositoryOperations()
    evidence = InMemoryDenialEvidenceSink()
    controller = CrossRepositoryAdmissionController(evidence_sink=evidence)

    with pytest.raises(CrossRepositoryDeniedError):
        controller.start_repository_operation(
            cross_repository_request(),
            datetime(2026, 8, 5, tzinfo=UTC),
            operations.discover,
        )

    assert operations.calls == []
    assert evidence.records[0].to_dict()["reason"] == "repository_mismatch"


def test_environment_flag_does_not_bypass_the_pre_discovery_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMRELAY_EVAL_ALLOW_CROSS_REPOSITORY", "1")
    operations = ForbiddenRepositoryOperations()
    controller = CrossRepositoryAdmissionController()

    with pytest.raises(CrossRepositoryDeniedError):
        controller.start_repository_operation(
            cross_repository_request(),
            datetime(2026, 8, 5, tzinfo=UTC),
            operations.clone,
        )

    assert operations.calls == []


class PermissiveAuthority:
    def authorize(self, request: RepositoryAccessRequest, now: datetime) -> AuthorizationResult:
        del now
        return AuthorizationResult(AuthorizationDecision.PERMITTED, request.policy_version)


def test_custom_authority_cannot_bypass_cross_repository_denial() -> None:
    operations = ForbiddenRepositoryOperations()
    controller = CrossRepositoryAdmissionController(authority=PermissiveAuthority())

    with pytest.raises(CrossRepositoryDeniedError):
        controller.start_repository_operation(
            cross_repository_request(),
            datetime(2026, 8, 5, tzinfo=UTC),
            operations.cache_lookup,
        )

    assert operations.calls == []


class MalformedAuthority:
    def authorize(self, request: RepositoryAccessRequest, now: datetime) -> object:
        del request, now
        return "permitted"

    def admit_and_start(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: object,
    ) -> tuple[object, None]:
        del request, now, operation
        return "permitted", None


def test_malformed_authority_result_fails_closed_before_repository_operation() -> None:
    operations = ForbiddenRepositoryOperations()
    controller = CrossRepositoryAdmissionController(authority=MalformedAuthority())
    request = cross_repository_request()
    request = RepositoryAccessRequest(
        request_id=request.request_id,
        task_repository_id=request.task_repository_id,
        requested_repository_id=request.task_repository_id,
        principal_id=request.principal_id,
        authorization_id=request.authorization_id,
        authorization_version=request.authorization_version,
        purpose_id=request.purpose_id,
        purpose_version=request.purpose_version,
        policy_version=request.policy_version,
        valid_from=request.valid_from,
        valid_until=request.valid_until,
        revocation_state=request.revocation_state,
        stage=request.stage,
    )

    with pytest.raises(CrossRepositoryDeniedError) as failure:
        controller.start_repository_operation(
            request, datetime(2026, 8, 5, tzinfo=UTC), operations.discover
        )

    assert failure.value.reason.value == "authorization_not_current"
    assert operations.calls == []


def test_cross_repository_cli_refusal_contains_no_repository_information(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["run", "--stage", "cross-repo"])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "cross_repository_stage_disabled" in output
    assert re.search(r"repository_[a-f0-9]{32}", output) is None
    assert "credential" not in output
