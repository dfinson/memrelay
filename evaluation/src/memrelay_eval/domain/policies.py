"""Domain lifecycle policy."""

from __future__ import annotations

from .errors import InvalidLifecycleTransitionError
from .states import RunState

_ALLOWED_TRANSITIONS = {
    RunState.PLANNED: frozenset({RunState.ASSIGNED}),
    RunState.ASSIGNED: frozenset({RunState.PROVISIONED}),
    RunState.PROVISIONED: frozenset({RunState.RUNNING}),
    RunState.RUNNING: frozenset({RunState.EXPORTED}),
    RunState.EXPORTED: frozenset({RunState.SCORED}),
    RunState.SCORED: frozenset({RunState.RECONCILED}),
    RunState.RECONCILED: frozenset({RunState.INCLUDED, RunState.EXCLUDED}),
    RunState.INCLUDED: frozenset(),
    RunState.EXCLUDED: frozenset(),
}


def validate_run_transition(previous: RunState, next_state: RunState) -> None:
    """Raise unless the requested edge is part of the frozen run graph."""

    if next_state not in _ALLOWED_TRANSITIONS[previous]:
        raise InvalidLifecycleTransitionError(
            f"invalid run transition: {previous.value} -> {next_state.value}"
        )
