from __future__ import annotations

from datetime import UTC, datetime

import pytest
from memrelay_eval.domain.entities import RunTransition
from memrelay_eval.domain.errors import InvalidLifecycleTransitionError
from memrelay_eval.domain.ids import RunId
from memrelay_eval.domain.policies import validate_run_transition
from memrelay_eval.domain.states import RunState

VALID_EDGES = (
    (RunState.PLANNED, RunState.ASSIGNED),
    (RunState.ASSIGNED, RunState.PROVISIONED),
    (RunState.PROVISIONED, RunState.RUNNING),
    (RunState.RUNNING, RunState.EXPORTED),
    (RunState.EXPORTED, RunState.SCORED),
    (RunState.SCORED, RunState.RECONCILED),
    (RunState.RECONCILED, RunState.INCLUDED),
    (RunState.RECONCILED, RunState.EXCLUDED),
)


@pytest.mark.parametrize(("previous", "next_state"), VALID_EDGES)
def test_lifecycle_accepts_every_frozen_edge(previous: RunState, next_state: RunState) -> None:
    transition = RunTransition(RunId.new(), previous, next_state, datetime.now(UTC))
    assert transition.next_state is next_state


@pytest.mark.parametrize(
    ("previous", "next_state"),
    [
        (previous, next_state)
        for previous in RunState
        for next_state in RunState
        if (previous, next_state) not in VALID_EDGES
    ],
)
def test_lifecycle_rejects_every_non_graph_edge(previous: RunState, next_state: RunState) -> None:
    with pytest.raises(InvalidLifecycleTransitionError):
        validate_run_transition(previous, next_state)
