from __future__ import annotations

import asyncio

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.adapters.inspect.task import (
    InspectTaskRequest,
    NativeTerminalRecord,
    SessionLimits,
)
from memrelay_eval.domain.errors import ExecutionAdapterError, ExecutionEvidenceConflictError
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.domain.states import AttemptTerminalKind
from memrelay_eval.orchestration.attempt import AttemptTerminalRecorder
from memrelay_eval.orchestration.inspect import InspectAttemptController


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


class FakeScheduler:
    def __init__(self, record: NativeTerminalRecord | Exception) -> None:
        self.record = record

    async def execute(self, task: InspectTaskRequest) -> NativeTerminalRecord:
        if isinstance(self.record, Exception):
            raise self.record
        return self.record


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
    assert len(terminal.evidence_refs) == 3
