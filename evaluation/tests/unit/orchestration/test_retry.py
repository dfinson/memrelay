from __future__ import annotations

from datetime import UTC, datetime

import pytest
from memrelay_eval.adapters.fakes import InMemoryLedger
from memrelay_eval.domain.entities import (
    ArtifactRef,
    Attempt,
    AttemptTerminal,
    FreshIsolationAttestation,
    Protocol,
    Run,
)
from memrelay_eval.domain.errors import RetryDeniedError
from memrelay_eval.domain.ids import AssignmentId, AttemptId, ProtocolId, RunId
from memrelay_eval.domain.states import (
    AttemptTerminalKind,
    ExposureClassification,
    RetryRequestPurpose,
)
from memrelay_eval.orchestration.retry import ExposureDecision, RetryAuthorizer


def _inputs() -> tuple[Protocol, Run, Attempt, AttemptTerminal, ExposureDecision, FreshIsolationAttestation]:
    run = Run(RunId.new(), AssignmentId.new())
    attempt = Attempt(AttemptId.new(), run.id)
    terminal = AttemptTerminal(
        attempt.id,
        run.id,
        AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
        datetime.now(UTC),
        "isolated workspace provisioning failed",
        (ArtifactRef.from_bytes(b"provisioning failure"),),
    )
    return (
        Protocol(ProtocolId.new(), allows_pre_exposure_infrastructure_retry=True),
        run,
        attempt,
        terminal,
        ExposureDecision(
            ExposureClassification.UNEXPOSED,
            (ArtifactRef.from_bytes(b"no task delivery or treatment access"),),
        ),
        FreshIsolationAttestation(True, (ArtifactRef.from_bytes(b"fresh roots"),)),
    )


def test_authorized_retry_creates_exactly_one_new_attempt_with_same_assignment() -> None:
    protocol, run, attempt, terminal, exposure, isolation = _inputs()
    ledger = InMemoryLedger()
    ledger.append_attempt_terminal(terminal)
    authorizer = RetryAuthorizer(ledger)

    retry = authorizer.authorize(
        protocol, run, attempt, terminal, exposure=exposure, isolation=isolation
    )

    assert retry.parent_attempt_id == attempt.id
    assert retry.attempt.id != attempt.id
    assert retry.attempt.run_id == run.id
    assert retry.assignment_id == run.assignment_id
    assert retry.parent_terminal is terminal
    assert retry.parent_terminal.evidence_refs == terminal.evidence_refs


def test_retry_rejects_a_terminal_that_disagrees_with_ledger_history() -> None:
    protocol, run, attempt, terminal, exposure, isolation = _inputs()
    ledger = InMemoryLedger()
    ledger_terminal = AttemptTerminal(
        terminal.attempt_id,
        terminal.run_id,
        AttemptTerminalKind.INFRASTRUCTURE_FAILED_POST_EXPOSURE,
        terminal.occurred_at,
        terminal.reason,
        terminal.evidence_refs,
    )
    ledger.append_attempt_terminal(ledger_terminal)

    with pytest.raises(RetryDeniedError) as error:
        RetryAuthorizer(ledger).authorize(
            protocol, run, attempt, terminal, exposure=exposure, isolation=isolation
        )

    assert error.value.code == "retry_terminal_not_authoritative"


@pytest.mark.parametrize(
    ("configure", "expected_code"),
    [
        (
            lambda protocol, terminal, exposure, isolation: Protocol(protocol.id, False),
            "retry_not_authorized_by_protocol",
        ),
        (
            lambda protocol, terminal, exposure, isolation: AttemptTerminal(
                terminal.attempt_id,
                terminal.run_id,
                AttemptTerminalKind.PROVIDER_UNAVAILABLE,
                terminal.occurred_at,
                terminal.reason,
                terminal.evidence_refs,
            ),
            "retry_terminal_not_pre_exposure_infrastructure_failure",
        ),
        (
            lambda protocol, terminal, exposure, isolation: ExposureDecision(
                ExposureClassification.AMBIGUOUS, exposure.evidence_refs
            ),
            "retry_exposure_not_conclusively_unexposed",
        ),
        (
            lambda protocol, terminal, exposure, isolation: FreshIsolationAttestation(False, ()),
            "retry_fresh_isolation_unattested",
        ),
    ],
)
def test_retry_fails_closed_for_any_missing_authorization_condition(
    configure: object, expected_code: str
) -> None:
    protocol, run, attempt, terminal, exposure, isolation = _inputs()
    changed = configure(protocol, terminal, exposure, isolation)  # type: ignore[operator]
    if isinstance(changed, Protocol):
        protocol = changed
    elif isinstance(changed, AttemptTerminal):
        terminal = changed
    elif isinstance(changed, ExposureDecision):
        exposure = changed
    else:
        isolation = changed

    ledger = InMemoryLedger()
    ledger.append_attempt_terminal(terminal)
    with pytest.raises(RetryDeniedError) as error:
        RetryAuthorizer(ledger).authorize(
            protocol, run, attempt, terminal, exposure=exposure, isolation=isolation
        )

    assert error.value.code == expected_code


@pytest.mark.parametrize(
    "purpose",
    (
        RetryRequestPurpose.BEST_OF_N,
        RetryRequestPurpose.REPEAT_UNTIL_SUCCESS,
        RetryRequestPurpose.FAVORABLE_SUBSTITUTION,
    ),
)
def test_favorable_substitution_requests_are_rejected(purpose: RetryRequestPurpose) -> None:
    protocol, run, attempt, terminal, exposure, isolation = _inputs()
    ledger = InMemoryLedger()
    ledger.append_attempt_terminal(terminal)

    with pytest.raises(RetryDeniedError) as error:
        RetryAuthorizer(ledger).authorize(
            protocol,
            run,
            attempt,
            terminal,
            exposure=exposure,
            isolation=isolation,
            purpose=purpose,
        )

    assert error.value.code == "retry_favorable_substitution_forbidden"
