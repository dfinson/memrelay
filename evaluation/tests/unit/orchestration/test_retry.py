from __future__ import annotations

from datetime import UTC, datetime
from threading import Barrier, Thread

import pytest
from memrelay_eval.adapters.fakes import InMemoryLedger, InMemoryTelemetry
from memrelay_eval.domain.entities import (
    ArtifactRef,
    Attempt,
    AttemptTerminal,
    FreshIsolationAttestation,
    InternalRetryPolicy,
    Protocol,
    Run,
)
from memrelay_eval.domain.errors import InternalRetryLimitExceededError, RetryDeniedError
from memrelay_eval.domain.ids import AssignmentId, AttemptId, ProtocolId, RunId
from memrelay_eval.domain.states import (
    AttemptTerminalKind,
    ExposureClassification,
    InternalRetrySubsystem,
    RetryRequestPurpose,
)
from memrelay_eval.orchestration.attempt import InternalRetryRecorder
from memrelay_eval.orchestration.retry import ExposureDecision, RetryAuthorizer


class _AuthorizationRaceLedger(InMemoryLedger):
    """Makes the legacy read-then-append authorization path deterministically race."""

    def __init__(self) -> None:
        super().__init__()
        self._append_barrier = Barrier(2)

    def append_retry_authorization(self, authorization: object) -> None:
        if self.retry_authorizations_for(authorization.run_id):  # type: ignore[union-attr]
            raise ValueError("retry_already_authorized_for_run")
        self._append_barrier.wait(timeout=2)
        self._retry_authorizations.append(authorization)  # type: ignore[arg-type]


class _InternalRetryRaceLedger(InMemoryLedger):
    """Makes the legacy count-then-append retry path deterministically race."""

    def __init__(self) -> None:
        super().__init__()
        self._count_barrier = Barrier(2)

    def internal_retries_for(
        self, attempt_id: AttemptId, subsystem: InternalRetrySubsystem
    ) -> tuple[object, ...]:
        records = super().internal_retries_for(attempt_id, subsystem)
        self._count_barrier.wait(timeout=2)
        return records


def _inputs() -> tuple[
    Protocol,
    Run,
    Attempt,
    AttemptTerminal,
    ExposureDecision,
    FreshIsolationAttestation,
]:
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
    assert retry.exposure_evidence_refs == exposure.evidence_refs
    assert retry.isolation_evidence_refs == isolation.evidence_refs


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


def test_retry_authorization_is_atomic_under_concurrent_requests() -> None:
    protocol, run, attempt, terminal, exposure, isolation = _inputs()
    ledger = _AuthorizationRaceLedger()
    ledger.append_attempt_terminal(terminal)
    authorizer = RetryAuthorizer(ledger)
    outcomes: list[object] = []

    def authorize() -> None:
        try:
            outcomes.append(
                authorizer.authorize(
                    protocol, run, attempt, terminal, exposure=exposure, isolation=isolation
                )
            )
        except RetryDeniedError as error:
            outcomes.append(error)

    threads = (Thread(target=authorize), Thread(target=authorize))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(ledger.retry_authorizations) == 1
    assert sum(isinstance(outcome, RetryDeniedError) for outcome in outcomes) == 1


def test_internal_retry_limit_is_atomic_under_concurrent_requests() -> None:
    policy = InternalRetryPolicy(InternalRetrySubsystem.SDK, maximum_retries=1)
    ledger = _InternalRetryRaceLedger()
    recorder = InternalRetryRecorder(AttemptId.new(), (policy,), ledger, InMemoryTelemetry())
    outcomes: list[object] = []

    def record() -> None:
        try:
            outcomes.append(recorder.record(InternalRetrySubsystem.SDK))
        except InternalRetryLimitExceededError as error:
            outcomes.append(error)

    threads = (Thread(target=record), Thread(target=record))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(ledger.internal_retries) == 1
    assert sum(isinstance(outcome, InternalRetryLimitExceededError) for outcome in outcomes) == 1
