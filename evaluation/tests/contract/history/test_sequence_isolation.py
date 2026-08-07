from __future__ import annotations

import pytest
from memrelay_eval.domain.entities import (
    ArtifactRef,
    DynamicSequenceCleanup,
    DynamicSequenceTerminal,
)
from memrelay_eval.domain.errors import InvalidAttemptTerminalError
from memrelay_eval.domain.ids import AttemptId, SequenceId
from memrelay_eval.domain.states import SequenceState


def test_terminal_and_cleanup_records_are_typed_and_evidence_backed() -> None:
    sequence_id = SequenceId.new()
    evidence = ArtifactRef.from_bytes(b"deterministic cleanup")
    terminal = DynamicSequenceTerminal(
        sequence_id, SequenceState.TERMINAL, (AttemptId.new(), AttemptId.new()), (evidence,)
    )
    cleanup = DynamicSequenceCleanup(sequence_id, SequenceState.CLEANED_UP, (evidence,))

    assert terminal.sequence_id == cleanup.sequence_id == sequence_id
    assert terminal.evidence_refs == cleanup.evidence_refs == (evidence,)

    with pytest.raises(InvalidAttemptTerminalError):
        DynamicSequenceCleanup(sequence_id, SequenceState.CLEANED_UP, ())

    with pytest.raises(InvalidAttemptTerminalError):
        DynamicSequenceTerminal(
            sequence_id, SequenceState.CLEANED_UP, (AttemptId.new(),), (evidence,)
        )
