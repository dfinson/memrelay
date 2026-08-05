from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from memrelay_eval.adapters.fakes import InMemoryLedger, InMemoryTelemetry
from memrelay_eval.domain.entities import ArtifactRef, AttemptTerminal
from memrelay_eval.domain.errors import AttemptTerminalAlreadyRecordedError
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.domain.states import AttemptTerminalKind
from memrelay_eval.orchestration.attempt import AttemptTerminalRecorder


def _terminal(kind: AttemptTerminalKind = AttemptTerminalKind.AGENT_FAILED) -> AttemptTerminal:
    return AttemptTerminal(
        attempt_id=AttemptId.new(),
        run_id=RunId.new(),
        classification=kind,
        occurred_at=datetime.now(UTC),
        reason="typed failure retained",
        evidence_refs=(ArtifactRef.from_bytes(b"partial native evidence"),),
    )


@pytest.mark.parametrize("kind", tuple(AttemptTerminalKind))
def test_every_frozen_terminal_kind_is_recorded_with_partial_evidence(
    kind: AttemptTerminalKind,
) -> None:
    ledger = InMemoryLedger()
    telemetry = InMemoryTelemetry()
    recorder = AttemptTerminalRecorder(ledger, telemetry)
    terminal = _terminal(kind)

    recorder.append(terminal)

    assert recorder.terminal_for(terminal.attempt_id) is terminal
    assert ledger.attempt_terminals == (terminal,)
    assert telemetry.terminals == (terminal,)
    assert ledger.history(terminal.run_id) == ()


def test_terminal_record_and_partial_evidence_are_immutable_and_append_once() -> None:
    ledger = InMemoryLedger()
    recorder = AttemptTerminalRecorder(ledger, InMemoryTelemetry())
    terminal = _terminal()
    recorder.append(terminal)
    fresh_recorder = AttemptTerminalRecorder(ledger, InMemoryTelemetry())
    assert fresh_recorder.terminal_for(terminal.attempt_id) is terminal

    with pytest.raises(FrozenInstanceError):
        terminal.reason = "replacement"  # type: ignore[misc]
    with pytest.raises(AttemptTerminalAlreadyRecordedError) as error:
        fresh_recorder.append(terminal)

    assert error.value.code == "attempt_terminal_already_recorded"
    assert recorder.terminal_for(terminal.attempt_id) is terminal
