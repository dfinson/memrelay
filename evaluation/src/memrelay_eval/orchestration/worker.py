"""Worker-facing intent and disposable-process coordination."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Protocol

from memrelay_eval.adapters.process.environment import (
    CredentialReference,
    build_process_environment,
)
from memrelay_eval.adapters.process.launcher import (
    DisposableProcessLauncher,
    ProcessLaunchRequest,
    ProcessRunReport,
)
from memrelay_eval.adapters.workspace.base import WorkspaceHandle
from memrelay_eval.domain.intents import IntentAck, IntentRejection, LedgerIntentType

from .limits import AttemptProcessLimiter


class WorkerIntentSink(Protocol):
    """Narrow process-boundary contract supplied to an isolated attempt worker."""

    def emit(self, intent: LedgerIntentType) -> IntentAck | IntentRejection: ...


class WorkerIntentEmitter:
    """Small worker helper that forwards opaque immutable intents to the control process."""

    def __init__(self, sink: WorkerIntentSink) -> None:
        self._sink = sink

    def emit(self, intent: LedgerIntentType) -> IntentAck | IntentRejection:
        return self._sink.emit(intent)


class DisposableAttemptWorker:
    """Coordinates one bounded role launch without retaining a reusable worker."""

    def __init__(self, launcher: DisposableProcessLauncher, limiter: AttemptProcessLimiter) -> None:
        self._launcher = launcher
        self._limiter = limiter

    def execute(
        self,
        request: ProcessLaunchRequest,
        workspace: WorkspaceHandle,
        *,
        credential_references: Sequence[CredentialReference] = (),
        credential_values: Mapping[str, str] | None = None,
    ) -> ProcessRunReport:
        if request.attempt_id != str(workspace.attempt_id):
            raise ValueError("process request must use the workspace attempt ID")
        environment = build_process_environment(
            request.role,
            runtime_environment=workspace.environment,
            credential_references=credential_references,
            credential_values=credential_values,
        )
        isolated_request = replace(
            request,
            cwd=workspace.workspace_root,
            environment=environment,
            socket_paths=(workspace.socket_path,),
        )
        with self._limiter.lease(request.attempt_id):
            return self._launcher.execute(isolated_request)
