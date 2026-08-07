"""Control-owned completion of an Inspect attempt and its independent evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from memrelay_eval.adapters.inspect.export import (
    ExecutionEvidence,
    persist_execution_evidence,
    reconcile_execution_evidence,
)
from memrelay_eval.adapters.inspect.task import InspectTaskRequest, NativeTerminalRecord
from memrelay_eval.domain.entities import AttemptTerminal
from memrelay_eval.domain.errors import (
    AgentParityMismatchError,
    ExecutionAdapterError,
    ExecutionEvidenceConflictError,
)
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.domain.states import AttemptTerminalKind

from .attempt import AttemptTerminalRecorder
from .parity import ParityPreflightEvidence, persist_parity_preflight


class InspectScheduler(Protocol):
    """The only scheduling surface accepted by the execution controller."""

    async def execute(self, task: InspectTaskRequest) -> NativeTerminalRecord: ...


class InspectAttemptController:
    """Preserves authority artifacts before appending one thin terminal ledger record."""

    def __init__(
        self,
        scheduler: InspectScheduler,
        store: ArtifactStorePort,
        recorder: AttemptTerminalRecorder,
    ) -> None:
        self._scheduler = scheduler
        self._store = store
        self._recorder = recorder

    async def execute(
        self,
        task: InspectTaskRequest,
        *,
        attempt_id: AttemptId,
        run_id: RunId,
        inspect_state: str,
        eval_bytes: bytes,
        inspect_export: dict[str, object],
    ) -> ExecutionEvidence:
        try:
            native = await self._scheduler.execute(task)
        except ExecutionAdapterError as error:
            native = NativeTerminalRecord(
                "failed",
                (),
                (),
                {},
                error.code,
            )
        evidence = persist_execution_evidence(
            self._store,
            inspect_state=inspect_state,
            eval_bytes=eval_bytes,
            inspect_export=inspect_export,
            native_terminal=native,
        )
        try:
            reconcile_execution_evidence(evidence, inspect_export)
        except ExecutionEvidenceConflictError:
            self._recorder.append(
                AttemptTerminal(
                    attempt_id,
                    run_id,
                    AttemptTerminalKind.EVIDENCE_INCOMPLETE,
                    datetime.now(UTC),
                    ExecutionEvidenceConflictError.code,
                    (
                        evidence.eval_artifact,
                        evidence.inspect_json_artifact,
                        evidence.native_terminal_artifact,
                    ),
                )
            )
            raise
        classification = {
            "succeeded": AttemptTerminalKind.SUCCEEDED,
            "cancelled": AttemptTerminalKind.CANCELLED_BY_CIRCUIT_BREAKER,
            "timed_out": AttemptTerminalKind.TIMED_OUT,
        }.get(native.state, AttemptTerminalKind.AGENT_FAILED)
        self._recorder.append(
            AttemptTerminal(
                attempt_id,
                run_id,
                classification,
                datetime.now(UTC),
                native.failure_code or native.state,
                (
                    evidence.eval_artifact,
                    evidence.inspect_json_artifact,
                    evidence.native_terminal_artifact,
                ),
            )
        )
        return evidence

    async def execute_after_parity(
        self,
        task: InspectTaskRequest,
        *,
        attempt_id: AttemptId,
        run_id: RunId,
        inspect_state: str,
        eval_bytes: bytes,
        inspect_export: dict[str, object],
        parity_preflight: ParityPreflightEvidence,
    ) -> ExecutionEvidence:
        """Deny mismatched pairs before the scheduler can deliver a task or infer."""
        from memrelay_eval.domain.entities import Attempt

        try:
            parity_preflight.require_execution_ready_for(Attempt(attempt_id, run_id))
        except AgentParityMismatchError as error:
            artifact = persist_parity_preflight(parity_preflight, self._store)
            self._recorder.append(
                AttemptTerminal(
                    attempt_id,
                    run_id,
                    AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
                    datetime.now(UTC),
                    error.code,
                    (artifact,),
                )
            )
            raise
        return await self.execute(
            task,
            attempt_id=attempt_id,
            run_id=run_id,
            inspect_state=inspect_state,
            eval_bytes=eval_bytes,
            inspect_export=inspect_export,
        )
