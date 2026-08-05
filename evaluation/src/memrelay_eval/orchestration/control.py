"""Pre-discovery admission control for repository-touching work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from threading import Lock
from typing import TypeVar, cast

from memrelay_eval.domain.errors import CrossRepositoryDeniedError
from memrelay_eval.domain.governance import (
    AuthorizationDecision,
    AuthorizationResult,
    DenialEvidence,
    DenyByDefaultRepositoryAuthorization,
    GovernanceDenialReason,
    RepositoryAccessRequest,
)
from memrelay_eval.domain.ports import DenialEvidencePort, RepositoryAuthorizationPort

_Result = TypeVar("_Result")


class InMemoryDenialEvidenceSink:
    """Deterministic local sink for conformance and CLI refusal evidence."""

    def __init__(self) -> None:
        self._records: list[DenialEvidence] = []

    def append_denial(self, evidence: DenialEvidence) -> None:
        self._records.append(evidence)

    @property
    def records(self) -> tuple[DenialEvidence, ...]:
        return tuple(self._records)


class CrossRepositoryAdmissionController:
    """Checks authority at entry and again immediately before starting work."""

    def __init__(
        self,
        *,
        authority: RepositoryAuthorizationPort | None = None,
        evidence_sink: DenialEvidencePort | None = None,
    ) -> None:
        self._deny_by_default = DenyByDefaultRepositoryAuthorization()
        self._authority = authority
        self._evidence_sink = evidence_sink or InMemoryDenialEvidenceSink()
        self._admission_lock = Lock()

    def authorize_at_entry(self, request: RepositoryAccessRequest, now: datetime) -> None:
        self._authorize(request, now)

    def start_repository_operation(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], _Result],
    ) -> _Result:
        """Atomically recheck governance before a synchronous repository operation."""

        self._authorize_local_policy(request, now)
        if self._authority is not None:
            result, operation_result = self._admit_and_start(request, now, operation)
            self._deny_if_needed(request, result)
            return cast(_Result, operation_result)
        with self._admission_lock:
            self._authorize_local_policy(request, now)
            return operation()

    async def start_repository_operation_async(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], Awaitable[_Result]],
    ) -> _Result:
        """Atomically recheck governance before an asynchronous repository operation."""

        self._authorize_local_policy(request, now)
        if self._authority is not None:
            result, operation_result = await self._admit_and_start_async(request, now, operation)
            self._deny_if_needed(request, result)
            return cast(_Result, operation_result)
        return await operation()

    def _authorize(self, request: RepositoryAccessRequest, now: datetime) -> None:
        self._authorize_local_policy(request, now)
        if self._authority is not None:
            self._deny_if_needed(request, self._authority_result(request, now))

    def _authorize_local_policy(self, request: RepositoryAccessRequest, now: datetime) -> None:
        self._deny_if_needed(request, self._deny_by_default.authorize(request, now))

    def _authority_result(
        self, request: RepositoryAccessRequest, now: datetime
    ) -> AuthorizationResult:
        if self._authority is None:
            return self._invalid_authority_result(request)
        authorize = getattr(self._authority, "authorize", None)
        if not callable(authorize):
            return self._invalid_authority_result(request)
        result = authorize(request, now)
        return self._validated_authority_result(request, result)

    def _admit_and_start(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], _Result],
    ) -> tuple[AuthorizationResult, _Result | None]:
        if self._authority is None:
            return self._invalid_authority_result(request), None
        admit_and_start = getattr(self._authority, "admit_and_start", None)
        if not callable(admit_and_start):
            return self._invalid_authority_result(request), None
        admission = admit_and_start(request, now, operation)
        if not isinstance(admission, tuple) or len(admission) != 2:
            return self._invalid_authority_result(request), None
        result, operation_result = admission
        return self._validated_authority_result(request, result), operation_result

    async def _admit_and_start_async(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], Awaitable[_Result]],
    ) -> tuple[AuthorizationResult, _Result | None]:
        if self._authority is None:
            return self._invalid_authority_result(request), None
        admit_and_start_async = getattr(self._authority, "admit_and_start_async", None)
        if not callable(admit_and_start_async):
            return self._invalid_authority_result(request), None
        admission = await admit_and_start_async(request, now, operation)
        if not isinstance(admission, tuple) or len(admission) != 2:
            return self._invalid_authority_result(request), None
        result, operation_result = admission
        return self._validated_authority_result(request, result), operation_result

    def _validated_authority_result(
        self, request: RepositoryAccessRequest, result: object
    ) -> AuthorizationResult:
        if (
            not isinstance(result, AuthorizationResult)
            or result.policy_version != request.policy_version
        ):
            return self._invalid_authority_result(request)
        return result

    def _invalid_authority_result(self, request: RepositoryAccessRequest) -> AuthorizationResult:
        return AuthorizationResult(
            AuthorizationDecision.DENIED,
            request.policy_version,
            GovernanceDenialReason.AUTHORIZATION_NOT_CURRENT,
        )

    def _deny_if_needed(
        self, request: RepositoryAccessRequest, result: AuthorizationResult
    ) -> None:
        if result.decision is AuthorizationDecision.DENIED:
            self._deny(request, result)

    def _deny(self, request: RepositoryAccessRequest, result: AuthorizationResult) -> None:
        evidence = DenialEvidence.from_result(request, result)
        self._evidence_sink.append_denial(evidence)
        raise CrossRepositoryDeniedError(result.reason)
