from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from threading import Lock

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
