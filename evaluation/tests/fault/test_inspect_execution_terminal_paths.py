from __future__ import annotations

import asyncio

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.adapters.inspect.task import (
    InspectTaskRequest,
    NativeTerminalRecord,
    SessionLimits,
)
from memrelay_eval.adapters.telemetry.semantics import SpanClass, TelemetryContext
from memrelay_eval.domain.errors import (
    ExecutionAdapterError,
    ExecutionEvidenceConflictError,
    SecretBoundaryViolationError,
)
from memrelay_eval.domain.identity import copilot_identity
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.domain.states import AttemptTerminalKind
from memrelay_eval.evidence.required import REQUIRED_NATIVE_EVIDENCE_KINDS
from memrelay_eval.orchestration.attempt import AttemptTerminalRecorder
from memrelay_eval.orchestration.inspect import InspectAttemptController
from memrelay_eval.orchestration.parity import ParityPreflightEvidence


def _task() -> InspectTaskRequest:
    return InspectTaskRequest(
        "opaque-task",
        {},
        "synthetic task",
        "native-model",
        {"tools": True},
        "unavailable",
        "unavailable",
        ("terminal",),
        ("read",),
        SessionLimits(10, 10, 10),
    )


def _telemetry_context(attempt_id: AttemptId, run_id: RunId) -> TelemetryContext:
    return TelemetryContext(
        experiment_id="exp_" + "1" * 32,
        protocol_id="protocol_" + "2" * 32,
        run_id=str(run_id),
        attempt_id=str(attempt_id),
        scenario_id="scenario_" + "3" * 32,
        stratum_id="product",
        history_mode="controlled",
        identity=copilot_identity(),
        evidence_class="native_evidence",
        exposure_state="unexposed",
        environment_fingerprint_sha256="a" * 64,
    )


class FakeScheduler:
    def __init__(self, record: NativeTerminalRecord | Exception) -> None:
        self.record = record
        self.calls = 0

    async def execute(self, task: InspectTaskRequest) -> NativeTerminalRecord:
        self.calls += 1
        if isinstance(self.record, Exception):
            raise self.record
        return self.record


def _preflight(
    attempt_id: AttemptId, *, mismatched_fields: tuple[str, ...] = ()
) -> ParityPreflightEvidence:
    return ParityPreflightEvidence(
        (str(attempt_id), str(AttemptId.new())),
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        mismatched_fields,
        runtime_model_locks_verified=True,
    )


@pytest.mark.parametrize(
    ("state", "failure"),
    (
        ("succeeded", None),
        ("cancelled", "cancelled"),
        ("timed_out", "timeout"),
        ("failed", "sdk_crash"),
    ),
)
def test_every_terminal_path_persists_three_independent_records(
    state: str, failure: str | None
) -> None:
    native = NativeTerminalRecord(state, ("event",), ("patch",), {"total_tokens": 1}, failure)
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    controller = InspectAttemptController(
        FakeScheduler(native), store, AttemptTerminalRecorder(ledger, InMemoryTelemetry())
    )
    attempt_id = AttemptId.new()
    run_id = RunId.new()

    evidence = asyncio.run(
        controller.execute(
            _task(),
            attempt_id=attempt_id,
            run_id=run_id,
            inspect_state=state,
            eval_bytes=b"inspect eval",
            inspect_export={"status": state},
        )
    )

    assert all(
        store.open_verified(reference)
        for reference in (
            evidence.eval_artifact,
            evidence.inspect_json_artifact,
            evidence.native_terminal_artifact,
        )
    )
    assert ledger.attempt_terminal_for(attempt_id).run_id == run_id


def test_crash_path_retains_partial_authority_evidence() -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    controller = InspectAttemptController(
        FakeScheduler(ExecutionAdapterError("sdk_crash", "synthetic crash")),
        store,
        AttemptTerminalRecorder(ledger, InMemoryTelemetry()),
    )
    attempt_id = AttemptId.new()
    run_id = RunId.new()

    evidence = asyncio.run(
        controller.execute(
            _task(),
            attempt_id=attempt_id,
            run_id=run_id,
            inspect_state="failed",
            eval_bytes=b"partial eval",
            inspect_export={"status": "failed"},
        )
    )

    assert evidence.native_terminal.failure_code == "sdk_crash"


def test_real_attempt_controller_emits_persists_and_reconciles_owned_spans() -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    telemetry = InMemoryTelemetry()
    controller = InspectAttemptController(
        FakeScheduler(NativeTerminalRecord("succeeded", ("event",), ("patch",), {})),
        store,
        AttemptTerminalRecorder(ledger, telemetry),
    )
    attempt_id = AttemptId.new()
    run_id = RunId.new()
    evidence = asyncio.run(
        controller.execute(
            _task(),
            attempt_id=attempt_id,
            run_id=run_id,
            inspect_state="succeeded",
            eval_bytes=b"inspect eval",
            inspect_export={"status": "succeeded"},
            telemetry_context=_telemetry_context(attempt_id, run_id),
            expected_telemetry_classes=frozenset(
                {
                    SpanClass.CONTROL_ASSIGNMENT,
                    SpanClass.COPILOT_SESSION,
                    SpanClass.COPILOT_MODEL_REQUEST,
                    SpanClass.INSPECT_EXPORT,
                    SpanClass.ARTIFACT_PERSISTENCE,
                    SpanClass.EVIDENCE_RECONCILIATION,
                }
            ),
        )
    )
    assert len(telemetry.semantic_spans) == 6
    assert ledger.attempt_terminal_for(attempt_id).classification is AttemptTerminalKind.SUCCEEDED
    assert evidence.inventory.artifacts


def test_missing_authority_derived_instrumentation_blocks_and_persists_partial_telemetry() -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    controller = InspectAttemptController(
        FakeScheduler(NativeTerminalRecord("succeeded", ("event",), ("patch",), {})),
        store,
        AttemptTerminalRecorder(ledger, InMemoryTelemetry()),
    )
    attempt_id = AttemptId.new()
    run_id = RunId.new()
    with pytest.raises(ExecutionEvidenceConflictError, match="telemetry"):
        asyncio.run(
            controller.execute(
                _task(),
                attempt_id=attempt_id,
                run_id=run_id,
                inspect_state="succeeded",
                eval_bytes=b"inspect eval",
                inspect_export={"status": "succeeded"},
                telemetry_context=_telemetry_context(attempt_id, run_id),
                expected_telemetry_classes=frozenset(
                    {SpanClass.CONTROL_ASSIGNMENT, SpanClass.MCP_TOOL_REQUEST}
                ),
            )
        )
    assert (
        ledger.attempt_terminal_for(attempt_id).classification
        is AttemptTerminalKind.EVIDENCE_INCOMPLETE
    )


def test_agent_visible_treatment_label_is_blocked_before_scheduler_execution() -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    scheduler = FakeScheduler(NativeTerminalRecord("succeeded", (), (), {}))
    controller = InspectAttemptController(
        scheduler, store, AttemptTerminalRecorder(ledger, InMemoryTelemetry())
    )
    attempt_id = AttemptId.new()
    task = InspectTaskRequest(
        "opaque-task",
        {},
        "Use the control treatment workflow.",
        "native-model",
        {"tools": True},
        "unavailable",
        "unavailable",
        ("terminal",),
        ("read",),
        SessionLimits(10, 10, 10),
    )

    with pytest.raises(SecretBoundaryViolationError) as raised:
        asyncio.run(
            controller.execute(
                task,
                attempt_id=attempt_id,
                run_id=RunId.new(),
                inspect_state="failed",
                eval_bytes=b"not reached",
                inspect_export={"status": "failed"},
            )
        )

    assert len(raised.value.evidence_refs) == 1
    assert scheduler.calls == 0
    assert ledger.attempt_terminal_for(attempt_id) is not None


def test_secret_failure_code_links_preserved_bundle_to_evidence_incomplete_terminal() -> None:
    value = "SK-" + ("x" * 24)
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    attempt_id = AttemptId.new()
    controller = InspectAttemptController(
        FakeScheduler(
            NativeTerminalRecord(
                "failed",
                ("event-reference",),
                ("patch-reference",),
                {"total_tokens": 3},
                value,
            )
        ),
        store,
        AttemptTerminalRecorder(ledger, InMemoryTelemetry()),
    )

    with pytest.raises(SecretBoundaryViolationError):
        asyncio.run(
            controller.execute(
                _task(),
                attempt_id=attempt_id,
                run_id=RunId.new(),
                inspect_state="failed",
                eval_bytes=b"safe eval",
                inspect_export={"status": "failed"},
            )
        )

    terminal = ledger.attempt_terminal_for(attempt_id)
    assert terminal is not None
    assert terminal.classification is AttemptTerminalKind.EVIDENCE_INCOMPLETE
    assert terminal.reason == SecretBoundaryViolationError.code
    assert len(terminal.evidence_refs) == len(REQUIRED_NATIVE_EVIDENCE_KINDS) + 1
    assert all(value.encode() not in store.open_verified(ref) for ref in terminal.evidence_refs)


def test_scan_failure_links_preserved_bundle_with_typed_terminal_code() -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    attempt_id = AttemptId.new()
    controller = InspectAttemptController(
        FakeScheduler(NativeTerminalRecord("succeeded", (), (), {})),
        store,
        AttemptTerminalRecorder(ledger, InMemoryTelemetry()),
    )

    with pytest.raises(SecretBoundaryViolationError):
        asyncio.run(
            controller.execute(
                _task(),
                attempt_id=attempt_id,
                run_id=RunId.new(),
                inspect_state="succeeded",
                eval_bytes=b"safe eval",
                inspect_export={"status": "succeeded"},
                secret_boundaries={"oversized": "x" * ((4 * 1024 * 1024) + 1)},
            )
        )

    terminal = ledger.attempt_terminal_for(attempt_id)
    assert terminal is not None
    assert terminal.classification is AttemptTerminalKind.EVIDENCE_INCOMPLETE
    assert terminal.reason == "evidence_scan_failed"
    assert len(terminal.evidence_refs) == len(REQUIRED_NATIVE_EVIDENCE_KINDS) + 1


def test_persistence_time_evidence_conflict_records_terminal_evidence() -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    controller = InspectAttemptController(
        FakeScheduler(NativeTerminalRecord("succeeded", (), (), {})),
        store,
        AttemptTerminalRecorder(ledger, InMemoryTelemetry()),
    )
    attempt_id = AttemptId.new()

    with pytest.raises(ExecutionEvidenceConflictError, match="override"):
        asyncio.run(
            controller.execute(
                _task(),
                attempt_id=attempt_id,
                run_id=RunId.new(),
                inspect_state="succeeded",
                eval_bytes=b"native eval",
                inspect_export={"status": "succeeded"},
                native_evidence={"usage": {}},
            )
        )

    terminal = ledger.attempt_terminal_for(attempt_id)
    assert terminal is not None
    assert terminal.classification is AttemptTerminalKind.EVIDENCE_INCOMPLETE
    assert len(terminal.evidence_refs) == 1


def test_terminal_authority_conflict_blocks_reconciliation_after_persistence() -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    controller = InspectAttemptController(
        FakeScheduler(NativeTerminalRecord("succeeded", (), (), {})),
        store,
        AttemptTerminalRecorder(ledger, InMemoryTelemetry()),
    )

    attempt_id = AttemptId.new()
    with pytest.raises(ExecutionEvidenceConflictError):
        asyncio.run(
            controller.execute(
                _task(),
                attempt_id=attempt_id,
                run_id=RunId.new(),
                inspect_state="failed",
                eval_bytes=b"partial eval",
                inspect_export={"status": "failed"},
            )
        )
    terminal = ledger.attempt_terminal_for(attempt_id)
    assert terminal is not None
    assert terminal.classification is AttemptTerminalKind.EVIDENCE_INCOMPLETE


@pytest.mark.parametrize(
    "failure_code",
    (
        "native_terminal_status_missing",
        "native_terminal_status_null",
        "native_terminal_status_unknown",
        "native_terminal_payload_invalid",
    ),
)
def test_malformed_native_terminal_blocks_reconciliation_with_partial_evidence(
    failure_code: str,
) -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    controller = InspectAttemptController(
        FakeScheduler(
            NativeTerminalRecord(
                "failed",
                ("partial-event",),
                (),
                {"total_tokens": 1},
                failure_code,
                corroborates_inspect=False,
            )
        ),
        store,
        AttemptTerminalRecorder(ledger, InMemoryTelemetry()),
    )
    attempt_id = AttemptId.new()

    with pytest.raises(ExecutionEvidenceConflictError, match="cannot independently corroborate"):
        asyncio.run(
            controller.execute(
                _task(),
                attempt_id=attempt_id,
                run_id=RunId.new(),
                inspect_state="failed",
                eval_bytes=b"partial eval",
                inspect_export={"status": "failed"},
            )
        )

    terminal = ledger.attempt_terminal_for(attempt_id)
    assert terminal is not None
    assert terminal.classification is AttemptTerminalKind.EVIDENCE_INCOMPLETE
    assert len(terminal.evidence_refs) == 14


def test_terminal_parity_failure_refuses_a_passing_replay_before_scheduler_execution() -> None:
    scheduler = FakeScheduler(NativeTerminalRecord("succeeded", (), (), {}))
    ledger = InMemoryLedger()
    controller = InspectAttemptController(
        scheduler,
        InMemoryArtifactStore(),
        AttemptTerminalRecorder(ledger, InMemoryTelemetry()),
    )
    attempt_id = AttemptId.new()
    run_id = RunId.new()

    from memrelay_eval.domain.errors import (
        AgentParityMismatchError,
        AttemptExecutionClaimDeniedError,
    )

    with pytest.raises(AgentParityMismatchError):
        asyncio.run(
            controller.execute_after_parity(
                _task(),
                attempt_id=attempt_id,
                run_id=run_id,
                inspect_state="succeeded",
                eval_bytes=b"must-not-run",
                inspect_export={"status": "succeeded"},
                parity_preflight=_preflight(attempt_id, mismatched_fields=("network_policy",)),
            )
        )
    assert scheduler.calls == 0
    assert ledger.attempt_terminal_for(attempt_id) is not None

    with pytest.raises(AttemptExecutionClaimDeniedError):
        asyncio.run(
            controller.execute_after_parity(
                _task(),
                attempt_id=attempt_id,
                run_id=run_id,
                inspect_state="succeeded",
                eval_bytes=b"must-not-run",
                inspect_export={"status": "succeeded"},
                parity_preflight=_preflight(attempt_id),
            )
        )

    assert scheduler.calls == 0


def test_concurrent_replays_claim_at_most_one_execution_before_scheduler_execution() -> None:
    class BlockingScheduler:
        def __init__(self) -> None:
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def execute(self, task: InspectTaskRequest) -> NativeTerminalRecord:
            del task
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            return NativeTerminalRecord("succeeded", (), (), {})

    scheduler = BlockingScheduler()
    controller = InspectAttemptController(
        scheduler,
        InMemoryArtifactStore(),
        AttemptTerminalRecorder(InMemoryLedger(), InMemoryTelemetry()),
    )
    attempt_id = AttemptId.new()
    run_id = RunId.new()
    preflight = _preflight(attempt_id)

    async def race() -> tuple[object, object]:
        first = asyncio.create_task(
            controller.execute_after_parity(
                _task(),
                attempt_id=attempt_id,
                run_id=run_id,
                inspect_state="succeeded",
                eval_bytes=b"concurrent-replay",
                inspect_export={"status": "succeeded"},
                parity_preflight=preflight,
            )
        )
        await scheduler.entered.wait()
        second = asyncio.create_task(
            controller.execute_after_parity(
                _task(),
                attempt_id=attempt_id,
                run_id=run_id,
                inspect_state="succeeded",
                eval_bytes=b"concurrent-replay",
                inspect_export={"status": "succeeded"},
                parity_preflight=preflight,
            )
        )
        await asyncio.sleep(0)
        scheduler.release.set()
        return tuple(await asyncio.gather(first, second, return_exceptions=True))  # type: ignore[return-value]

    outcomes = asyncio.run(race())

    from memrelay_eval.domain.errors import AttemptExecutionClaimDeniedError

    assert scheduler.calls == 1
    assert sum(isinstance(outcome, AttemptExecutionClaimDeniedError) for outcome in outcomes) == 1
