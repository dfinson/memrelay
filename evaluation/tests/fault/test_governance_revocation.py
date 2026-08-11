from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread

import pytest
from memrelay_eval.domain.errors import CrossRepositoryDeniedError, StageControlError
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
    RunId,
)
from memrelay_eval.orchestration.control import CrossRepositoryAdmissionController
from memrelay_eval.orchestration.limits import (
    CircuitBreakerAdmissionController,
    CircuitBreakerReason,
    FrozenLimitEnvelope,
)


class RevocableAuthority:
    def __init__(self) -> None:
        self._lock = Lock()
        self.revoked = False
        self.calls = 0

    def authorize(self, request: RepositoryAccessRequest, now: datetime) -> AuthorizationResult:
        del now
        with self._lock:
            self.calls += 1
            return self._result(request)

    def admit_and_start(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], object],
    ) -> tuple[AuthorizationResult, object | None]:
        """Fake the atomic future-authority contract required by Story 7.4."""
        del now
        with self._lock:
            self.calls += 1
            result = self._result(request)
            if result.decision is AuthorizationDecision.DENIED:
                return result, None
            return result, operation()

    def revoke(self) -> None:
        with self._lock:
            self.revoked = True

    def _result(self, request: RepositoryAccessRequest) -> AuthorizationResult:
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
    authority.revoke()

    with pytest.raises(CrossRepositoryDeniedError) as failure:
        controller.start_repository_operation(
            request,
            datetime(2026, 8, 5, tzinfo=UTC),
            lambda: operations_started.append("started"),
        )

    assert failure.value.reason is GovernanceDenialReason.AUTHORIZATION_REVOKED
    assert operations_started == []
    assert authority.calls == 2


def test_atomic_authority_handoff_starts_only_after_a_permitted_decision() -> None:
    authority = RevocableAuthority()
    controller = CrossRepositoryAdmissionController(authority=authority)
    operations_started: list[str] = []

    controller.start_repository_operation(
        ordinary_request(),
        datetime(2026, 8, 5, tzinfo=UTC),
        lambda: operations_started.append("started"),
    )

    assert operations_started == ["started"]
    assert authority.calls == 1


class AsyncRevocableAuthority:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.revoked = False

    def authorize(self, request: RepositoryAccessRequest, now: datetime) -> AuthorizationResult:
        del now
        return self._result(request)

    async def admit_and_start_async(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], Awaitable[object]],
    ) -> tuple[AuthorizationResult, object | None]:
        del now
        async with self._lock:
            result = self._result(request)
            if result.decision is AuthorizationDecision.DENIED:
                return result, None
            return result, await operation()

    async def revoke(self) -> None:
        async with self._lock:
            self.revoked = True

    def _result(self, request: RepositoryAccessRequest) -> AuthorizationResult:
        if self.revoked:
            return AuthorizationResult(
                AuthorizationDecision.DENIED,
                request.policy_version,
                GovernanceDenialReason.AUTHORIZATION_REVOKED,
            )
        return AuthorizationResult(AuthorizationDecision.PERMITTED, request.policy_version)


def test_revocation_before_async_start_prevents_repository_operation() -> None:
    authority = AsyncRevocableAuthority()
    controller = CrossRepositoryAdmissionController(authority=authority)
    operations_started: list[str] = []
    request = ordinary_request()

    asyncio.run(authority.revoke())

    async def operation() -> None:
        operations_started.append("started")

    with pytest.raises(CrossRepositoryDeniedError) as failure:
        asyncio.run(
            controller.start_repository_operation_async(
                request, datetime(2026, 8, 5, tzinfo=UTC), operation
            )
        )

    assert failure.value.reason is GovernanceDenialReason.AUTHORIZATION_REVOKED
    assert operations_started == []


def test_revoked_circuit_breaker_prevents_later_repository_admission() -> None:
    run_id = RunId.new()
    breaker = CircuitBreakerAdmissionController(
        stage_id="stage-1",
        stage_envelope=FrozenLimitEnvelope(
            scope="stage",
            source_sha256="a" * 64,
            limits={"copilot_tokens": 10},
        ),
        run_envelopes={
            run_id: FrozenLimitEnvelope(
                scope="run",
                source_sha256="b" * 64,
                limits={"copilot_tokens": 10},
            )
        },
    )
    breaker.trip(CircuitBreakerReason.GOVERNANCE_REVOKED)
    controller = CrossRepositoryAdmissionController(
        authority=RevocableAuthority(),
        circuit_breaker=breaker,
    )
    operations_started: list[str] = []

    with pytest.raises(StageControlError) as failure:
        controller.start_repository_operation(
            ordinary_request(),
            datetime(2026, 8, 5, tzinfo=UTC),
            lambda: operations_started.append("started"),
        )

    assert failure.value.code == "circuit_breaker_new_attempts_stopped"
    assert operations_started == []


def test_trip_after_external_start_claim_drains_existing_operation() -> None:
    run_id = RunId.new()
    breaker = CircuitBreakerAdmissionController(
        stage_id="stage-1",
        stage_envelope=FrozenLimitEnvelope(
            scope="stage",
            source_sha256="a" * 64,
            limits={"copilot_tokens": 10},
        ),
        run_envelopes={
            run_id: FrozenLimitEnvelope(
                scope="run",
                source_sha256="b" * 64,
                limits={"copilot_tokens": 10},
            )
        },
    )
    controller = CrossRepositoryAdmissionController(circuit_breaker=breaker)
    body_entered = Event()
    allow_completion = Event()
    operations_started: list[str] = []

    def operation() -> None:
        operations_started.append("started")
        body_entered.set()
        assert allow_completion.wait(timeout=5)

    worker = Thread(
        target=lambda: controller.start_repository_operation(
            ordinary_request(),
            datetime(2026, 8, 5, tzinfo=UTC),
            operation,
        )
    )
    worker.start()
    assert body_entered.wait(timeout=5)

    breaker.trip(CircuitBreakerReason.GOVERNANCE_REVOKED)

    assert breaker.state.value == "draining"
    assert breaker.status_projection()["active_external_operations"] == 1
    allow_completion.set()
    worker.join(timeout=5)
    assert worker.is_alive() is False
    assert operations_started == ["started"]
    assert breaker.state.value == "closed"
