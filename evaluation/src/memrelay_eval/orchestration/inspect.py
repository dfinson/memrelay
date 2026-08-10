"""Control-owned completion of an Inspect attempt and its independent evidence."""

from __future__ import annotations

from collections.abc import Mapping
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
from memrelay_eval.adapters.telemetry.reconcile import (
    persist_telemetry_evidence,
    reconcile_telemetry,
)
from memrelay_eval.adapters.telemetry.semantics import (
    SpanClass,
    TelemetryAttemptEmitter,
    TelemetryContext,
)
from memrelay_eval.domain.entities import AttemptTerminal
from memrelay_eval.domain.errors import (
    AgentParityMismatchError,
    ExecutionAdapterError,
    ExecutionEvidenceConflictError,
    SecretBoundaryViolationError,
)
from memrelay_eval.domain.identity import copilot_identity, identity_for_span_class
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.domain.states import AttemptTerminalKind
from memrelay_eval.evidence.costs import publish_native_quantity_ledger
from memrelay_eval.evidence.required import require_unpaid_conformance_ports
from memrelay_eval.evidence.secret_scan import SecretScanFinding, require_secret_boundary_clear

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
        telemetry_context: TelemetryContext | None = None,
        expected_telemetry_classes: frozenset[SpanClass] | None = None,
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
            telemetry_context=telemetry_context,
            expected_telemetry_classes=expected_telemetry_classes,
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
        telemetry_context: TelemetryContext | None = None,
        expected_telemetry_classes: frozenset[SpanClass] | None = None,
    ) -> ExecutionEvidence:
        emitter = (
            TelemetryAttemptEmitter(telemetry_context, self._recorder.telemetry)
            if telemetry_context is not None
            else None
        )
        _emit_boundary(
            emitter,
            SpanClass.CONTROL_ASSIGNMENT,
            failure_code=None,
        )
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
                    _secret_terminal_code(error.findings),
                    (finding_ref,),
                )
            )
            raise SecretBoundaryViolationError(error.findings, (finding_ref,)) from error
        try:
            scheduler_started = datetime.now(UTC)
            native = await self._scheduler.execute(task)
            _emit_boundary(
                emitter,
                SpanClass.COPILOT_SESSION,
                started_at=scheduler_started,
                failure_code=native.failure_code,
            )
            _emit_boundary(
                emitter,
                SpanClass.COPILOT_MODEL_REQUEST,
                started_at=scheduler_started,
                failure_code=native.failure_code,
            )
        except ExecutionAdapterError as error:
            native = NativeTerminalRecord(
                "failed",
                (),
                (),
                {},
                error.code,
            )
            _emit_boundary(
                emitter,
                SpanClass.COPILOT_SESSION,
                started_at=scheduler_started,
                failure_code=error.code,
            )
            _emit_boundary(
                emitter,
                SpanClass.COPILOT_MODEL_REQUEST,
                started_at=scheduler_started,
                failure_code=error.code,
            )
        try:
            export_started = datetime.now(UTC)
            evidence = persist_execution_evidence(
                self._store,
                inspect_state=inspect_state,
                eval_bytes=eval_bytes,
                inspect_export=inspect_export,
                native_terminal=native,
                native_evidence=native_evidence,
                secret_boundaries=secret_boundaries,
            )
            _emit_boundary(
                emitter,
                SpanClass.INSPECT_EXPORT,
                started_at=export_started,
                failure_code=native.failure_code,
            )
            publish_native_quantity_ledger(
                attempt_id=attempt_id,
                run_id=run_id,
                identity=copilot_identity(),
                source_authority="native_provider",
                source_ref="native_sdk_usage",
                source_evidence=evidence.inventory.artifacts["usage"],
                raw_quantities=_copilot_raw_quantities(native.usage),
                instrumentation_active=True,
                artifact_store=self._store,
                ledger=self._recorder.ledger,
                observed_at=datetime.now(UTC),
            )
            _emit_boundary(
                emitter,
                SpanClass.ARTIFACT_PERSISTENCE,
                started_at=export_started,
                failure_code=native.failure_code,
            )
        except SecretBoundaryViolationError as error:
            self._recorder.append(
                AttemptTerminal(
                    attempt_id,
                    run_id,
                    AttemptTerminalKind.EVIDENCE_INCOMPLETE,
                    datetime.now(UTC),
                    _secret_terminal_code(error.findings),
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
            reconciliation_started = datetime.now(UTC)
            reconcile_execution_evidence(evidence, inspect_export)
            _emit_boundary(
                emitter,
                SpanClass.EVIDENCE_RECONCILIATION,
                started_at=reconciliation_started,
                failure_code=native.failure_code,
            )
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
        if emitter is not None:
            telemetry_reconciliation = reconcile_telemetry(
                emitter.spans,
                expected_classes=expected_telemetry_classes or emitter.expected_classes,
                collector_shutdown_verified=True,
            )
            telemetry_ref = persist_telemetry_evidence(
                self._store, emitter.spans, telemetry_reconciliation
            )
            if not telemetry_reconciliation.complete:
                self._recorder.append(
                    AttemptTerminal(
                        attempt_id,
                        run_id,
                        AttemptTerminalKind.EVIDENCE_INCOMPLETE,
                        datetime.now(UTC),
                        "telemetry_reconciliation_failed",
                        (*evidence.inventory.artifacts.values(), telemetry_ref),
                    )
                )
                raise ExecutionEvidenceConflictError("telemetry_reconciliation_failed")
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
        telemetry_context: TelemetryContext | None = None,
        expected_telemetry_classes: frozenset[SpanClass] | None = None,
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
            telemetry_context=telemetry_context,
            expected_telemetry_classes=expected_telemetry_classes,
        )


def _secret_terminal_code(findings: tuple[object, ...]) -> str:
    typed = tuple(finding for finding in findings if isinstance(finding, SecretScanFinding))
    if any(
        finding.detector.startswith("scan_")
        or finding.detector.endswith("_scan_failed")
        or finding.detector == "base64_scan_limit_exceeded"
        for finding in typed
    ):
        return "evidence_scan_failed"
    return SecretBoundaryViolationError.code


def _emit_boundary(
    emitter: TelemetryAttemptEmitter | None,
    span_class: SpanClass,
    *,
    started_at: datetime | None = None,
    failure_code: str | None,
) -> None:
    if emitter is None:
        return
    ended_at = datetime.now(UTC)
    emitter.record(
        span_class,
        started_at=started_at or ended_at,
        ended_at=ended_at,
        failure_code=failure_code or None,
        identity=identity_for_span_class(span_class.value),
    )


def _copilot_raw_quantities(usage: Mapping[str, object] | object) -> dict[str, int]:
    """Accept only exact integer fields exposed by the native SDK usage authority."""

    if not isinstance(usage, Mapping):
        return {}
    fields = {
        "input_tokens": "input_tokens",
        "cached_input_tokens": "cached_input_tokens",
        "cache_write_tokens": "cache_write_tokens",
        "output_tokens": "output_tokens",
        "reasoning_tokens": "reasoning_tokens",
        "ai_credits": "ai_credits",
        "tool_calls": "tool_calls",
        "requests": "requests",
        "quota_rejections": "quota_rejections",
        "throttles": "throttles",
        "resets": "resets",
        "allowance": "allowance",
        "billing_periods": "billing_periods",
    }
    return {
        target: value
        for source, target in fields.items()
        if isinstance((value := usage.get(source)), int) and not isinstance(value, bool)
    }
