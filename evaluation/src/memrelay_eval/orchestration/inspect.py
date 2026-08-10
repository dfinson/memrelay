"""Control-owned completion of an Inspect attempt and its independent evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from memrelay_eval.adapters.inspect.export import (
    ExecutionEvidence,
    persist_execution_conflict_finding,
    persist_execution_evidence,
    persist_secret_boundary_findings,
    reconcile_execution_evidence,
)
from memrelay_eval.adapters.inspect.task import InspectTaskRequest, NativeTerminalRecord
from memrelay_eval.domain.entities import AttemptTerminal
from memrelay_eval.domain.errors import (
    AgentParityMismatchError,
    ExecutionAdapterError,
    ExecutionEvidenceConflictError,
    SecretBoundaryViolationError,
)
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.domain.states import AttemptTerminalKind
from memrelay_eval.evidence.required import require_unpaid_conformance_ports
from memrelay_eval.evidence.secret_scan import require_secret_boundary_clear

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
        require_unpaid_conformance_ports(store, recorder.ledger, recorder.telemetry)

    async def execute(
        self,
        task: InspectTaskRequest,
        *,
        attempt_id: AttemptId,
        run_id: RunId,
        inspect_state: str,
        eval_bytes: bytes,
        inspect_export: dict[str, object],
        native_evidence: dict[str, object] | None = None,
        secret_boundaries: dict[str, object] | None = None,
    ) -> ExecutionEvidence:
        self._recorder.claim_execution(attempt_id, run_id)
        return await self._execute_claimed(
            task,
            attempt_id=attempt_id,
            run_id=run_id,
            inspect_state=inspect_state,
            eval_bytes=eval_bytes,
            inspect_export=inspect_export,
            native_evidence=native_evidence,
            secret_boundaries=secret_boundaries,
        )

    async def _execute_claimed(
        self,
        task: InspectTaskRequest,
        *,
        attempt_id: AttemptId,
        run_id: RunId,
        inspect_state: str,
        eval_bytes: bytes,
        inspect_export: dict[str, object],
        native_evidence: dict[str, object] | None = None,
        secret_boundaries: dict[str, object] | None = None,
    ) -> ExecutionEvidence:
        agent_visible = {
            "prompt": task.prompt,
            "metadata": task.metadata,
            "capabilities": task.capabilities,
            "tools": task.tools,
            "permissions": task.permissions,
        }
        try:
            require_secret_boundary_clear({"agent_visible.task": agent_visible})
        except SecretBoundaryViolationError as error:
            finding_ref = persist_secret_boundary_findings(self._store, error.findings)
            self._recorder.append(
                AttemptTerminal(
                    attempt_id,
                    run_id,
                    AttemptTerminalKind.EVIDENCE_INCOMPLETE,
                    datetime.now(UTC),
                    error.code,
                    (finding_ref,),
                )
            )
            raise SecretBoundaryViolationError(error.findings, (finding_ref,)) from error
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
        try:
            evidence = persist_execution_evidence(
                self._store,
                inspect_state=inspect_state,
                eval_bytes=eval_bytes,
                inspect_export=inspect_export,
                native_terminal=native,
                native_evidence=native_evidence,
                secret_boundaries=secret_boundaries,
            )
        except SecretBoundaryViolationError as error:
            self._recorder.append(
                AttemptTerminal(
                    attempt_id,
                    run_id,
                    AttemptTerminalKind.EVIDENCE_INCOMPLETE,
                    datetime.now(UTC),
                    error.code,
                    tuple(error.evidence_refs),
                )
            )
            raise
        except ExecutionEvidenceConflictError:
            conflict_ref = persist_execution_conflict_finding(self._store)
            self._recorder.append(
                AttemptTerminal(
                    attempt_id,
                    run_id,
                    AttemptTerminalKind.EVIDENCE_INCOMPLETE,
                    datetime.now(UTC),
                    ExecutionEvidenceConflictError.code,
                    (conflict_ref,),
                )
            )
            raise
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
                    (*evidence.inventory.artifacts.values(),),
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
                (*evidence.inventory.artifacts.values(),),
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
        native_evidence: dict[str, object] | None = None,
        secret_boundaries: dict[str, object] | None = None,
    ) -> ExecutionEvidence:
        """Deny mismatched pairs before the scheduler can deliver a task or infer."""
        from memrelay_eval.domain.entities import Attempt

        self._recorder.claim_execution(attempt_id, run_id)
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
        return await self._execute_claimed(
            task,
            attempt_id=attempt_id,
            run_id=run_id,
            inspect_state=inspect_state,
            eval_bytes=eval_bytes,
            inspect_export=inspect_export,
            native_evidence=native_evidence,
            secret_boundaries=secret_boundaries,
        )
