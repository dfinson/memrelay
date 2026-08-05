"""Pre-discovery admission control for repository-touching work."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import Lock
from typing import TypeVar

from memrelay_eval.domain.errors import CrossRepositoryDeniedError
from memrelay_eval.domain.governance import (
    AuthorizationDecision,
    AuthorizationResult,
    DenialEvidence,
    DenyByDefaultRepositoryAuthorization,
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
        """Recheck under admission serialization before any repository operation starts."""

        with self._admission_lock:
            self._authorize(request, now)
            return operation()

    def _authorize(self, request: RepositoryAccessRequest, now: datetime) -> None:
        result = self._deny_by_default.authorize(request, now)
        if result.decision is AuthorizationDecision.DENIED:
            self._deny(request, result)
        if self._authority is None:
            return
        result = self._authority.authorize(request, now)
        if result.decision is AuthorizationDecision.DENIED:
            self._deny(request, result)

    def _deny(self, request: RepositoryAccessRequest, result: AuthorizationResult) -> None:
        evidence = DenialEvidence.from_result(request, result)
        self._evidence_sink.append_denial(evidence)
        raise CrossRepositoryDeniedError(result.reason)
