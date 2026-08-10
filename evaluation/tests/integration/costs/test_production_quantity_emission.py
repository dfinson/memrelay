from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.adapters.inspect.task import (
    InspectTaskRequest,
    NativeTerminalRecord,
    SessionLimits,
)
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.orchestration.attempt import AttemptTerminalRecorder
from memrelay_eval.orchestration.inspect import InspectAttemptController


class NativeScheduler:
    """A production-controller scheduler seam with deterministic native records."""

    def __init__(self, record: NativeTerminalRecord) -> None:
        self._record = record

    async def execute(self, task: InspectTaskRequest) -> NativeTerminalRecord:
        del task
        return self._record


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


@pytest.mark.parametrize(
    ("state", "failure_code", "usage", "expected_input"),
    (
        ("succeeded", None, {"input_tokens": 0, "output_tokens": 3}, 0),
        ("failed", "sdk_failed", {}, "unavailable"),
        ("timed_out", "timeout", {}, "unavailable"),
    ),
)
def test_inspect_controller_publishes_copilot_quantity_evidence_for_each_terminal_path(
    state: str,
    failure_code: str | None,
    usage: Mapping[str, int],
    expected_input: int | str,
) -> None:
    attempt_id = AttemptId.new()
    run_id = RunId.new()
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    controller = InspectAttemptController(
        NativeScheduler(NativeTerminalRecord(state, ("event",), ("patch",), usage, failure_code)),
        store,
        AttemptTerminalRecorder(ledger, InMemoryTelemetry()),
    )

    asyncio.run(
        controller.execute(
            _task(),
            attempt_id=attempt_id,
            run_id=run_id,
            inspect_state=state,
            eval_bytes=b"inspect eval",
            inspect_export={"status": state},
        )
    )

    links = ledger.cost_ledger_entries_for(attempt_id)
    assert links and {link.logical_ledger for link in links} == {"copilot_subscription"}
    entries = [json.loads(store.open_verified(link.artifact_ref)) for link in links]
    by_unit = {entry["unit"]: entry for entry in entries}
    assert by_unit["input_token"]["quantity"] == expected_input
    assert by_unit["cached_input_token"]["quantity"] == "unavailable"
    assert by_unit["input_token"]["source_sha256"]
    assert by_unit["input_token"]["measurement_status"] in {"metered", "unavailable"}
