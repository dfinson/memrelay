"""Domain lifecycle policy."""

from __future__ import annotations

from .errors import InvalidLifecycleTransitionError
from .states import AttemptTerminalKind, RunState

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


def retry_eligibility_denial_code(
    protocol: Protocol,
    terminal: AttemptTerminal,
    exposure: ExposureDecision | None,
    isolation: FreshIsolationAttestation | None,
) -> str | None:
    """Return the sole domain-owned denial code for a retry eligibility request."""

    if not protocol.allows_pre_exposure_infrastructure_retry:
        return "retry_not_authorized_by_protocol"
    if terminal.classification is not AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE:
        return "retry_terminal_not_pre_exposure_infrastructure_failure"
    if exposure is None or not exposure.is_conclusively_unexposed:
        return "retry_exposure_not_conclusively_unexposed"
    if isolation is None or not isolation.is_conclusive:
        return "retry_fresh_isolation_unattested"
    return None


def is_retryable_terminal(classification: AttemptTerminalKind) -> bool:
    """Return whether the frozen protocol allows this terminal class one retry."""

    return classification is AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE
