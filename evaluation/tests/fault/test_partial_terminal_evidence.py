from __future__ import annotations

from datetime import UTC, datetime

import pytest
from memrelay_eval.adapters.fakes import InMemoryLedger, InMemoryTelemetry
from memrelay_eval.domain.entities import ArtifactRef, AttemptTerminal
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.domain.states import AttemptTerminalKind
from memrelay_eval.orchestration.attempt import AttemptTerminalRecorder


@pytest.mark.parametrize(
    "kind",
    (
        AttemptTerminalKind.TIMED_OUT,
        AttemptTerminalKind.PROVIDER_UNAVAILABLE,
        AttemptTerminalKind.QUOTA_EXHAUSTED,
        AttemptTerminalKind.GRADER_FAILED,
        AttemptTerminalKind.EVIDENCE_INCOMPLETE,
        AttemptTerminalKind.CANCELLED_BY_CIRCUIT_BREAKER,
        AttemptTerminalKind.INFRASTRUCTURE_FAILED_POST_EXPOSURE,
    ),
)
def test_fault_terminal_preserves_available_evidence_without_claiming_durability(
    kind: AttemptTerminalKind,
) -> None:
    terminal = AttemptTerminal(
        AttemptId.new(),
        RunId.new(),
        kind,
        datetime.now(UTC),
        "interrupted after partial evidence",
        (ArtifactRef.from_bytes(b"partial evidence"),),
    )
    ledger = InMemoryLedger()
    recorder = AttemptTerminalRecorder(ledger, InMemoryTelemetry())

    recorder.append(terminal)

    assert ledger.attempt_terminals == (terminal,)
    assert ledger.eligible_for_paid_or_study is False
