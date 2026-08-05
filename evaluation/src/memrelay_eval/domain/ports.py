"""Domain-owned ports; concrete SDK objects stop at adapter boundaries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Protocol, TypeVar

from .entities import (
    ArtifactLink,
    ArtifactManifest,
    ArtifactRef,
    AttemptTerminal,
    InclusionDecision,
    RunTransition,
    TelemetryObservation,
)
from .governance import AuthorizationResult, DenialEvidence, RepositoryAccessRequest
from .ids import AssignmentId, RunId

_Result = TypeVar("_Result")


class LedgerPort(Protocol):
    def append_transition(self, transition: RunTransition) -> None: ...
    def append_artifact_link(self, link: ArtifactLink) -> None: ...
    def append_inclusion(self, decision: InclusionDecision) -> None: ...
    def history(self, run_id: RunId) -> Sequence[RunTransition]: ...


class ArtifactStorePort(Protocol):
    def put_bytes(self, data: bytes, *, media_type: str, classification: str) -> ArtifactRef: ...
    def open_verified(self, artifact: ArtifactRef) -> bytes: ...
    def write_manifest(self, manifest: ArtifactManifest) -> ArtifactRef: ...


class ExecutionAuthorityPort(Protocol):
    async def execute(self, task: object, attempt: object) -> object: ...


class AgentRuntimePort(Protocol):
    async def list_models(self) -> object: ...
    async def run_session(self, session: object) -> object: ...


class TreatmentPort(Protocol):
    async def provision(self, spec: object) -> object: ...
    async def restore_history(self, handle: object, history: object) -> None: ...
    async def collect_state(self, handle: object) -> Sequence[ArtifactRef]: ...
    async def close(self, handle: object) -> None: ...


class WorkspacePort(Protocol):
    async def create(self, spec: object) -> object: ...
    async def freeze(self, handle: object) -> object: ...
    async def destroy(self, handle: object) -> object: ...


class AssignmentPort(Protocol):
    def assign(self, request: object) -> object: ...
    def resolve_for_provisioning(self, assignment_id: AssignmentId) -> object: ...


class GraderPort(Protocol):
    async def grade(self, snapshot: object, contract: object) -> object: ...


class TelemetryPort(Protocol):
    def emit(self, observation: TelemetryObservation) -> None: ...
    def start_attempt(self, context: object) -> None: ...
    def finish_attempt(self, terminal: AttemptTerminal) -> None: ...
    def flush(self, timeout_seconds: float) -> object: ...


class ReconciliationPort(Protocol):
    def reconcile(self, run: object, required: object) -> object: ...


class RepositoryAuthorizationPort(Protocol):
    """Owns the atomic governance admission boundary for repository work.

    A future qualified authority must decide admission and invoke ``operation`` in
    the same authority-owned critical section. It must never invoke ``operation``
    for a denied admission; controller result validation cannot undo a repository
    operation that an authority has already started.
    """

    def authorize(self, request: RepositoryAccessRequest, now: datetime) -> AuthorizationResult: ...

    def admit_and_start(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], _Result],
    ) -> tuple[AuthorizationResult, _Result | None]:
        """Recheck revocation and invoke operation while admission remains closed."""
        ...

    async def admit_and_start_async(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], Awaitable[_Result]],
    ) -> tuple[AuthorizationResult, _Result | None]:
        """Recheck revocation and await operation while admission remains closed."""
        ...


class DenialEvidencePort(Protocol):
    """Records only the privacy-minimized denial evidence projection."""

    def append_denial(self, evidence: DenialEvidence) -> None: ...
