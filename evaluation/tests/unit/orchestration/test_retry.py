from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from threading import Barrier, Thread

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.domain.entities import (
    ArtifactRef,
    Attempt,
    AttemptTerminal,
    ExposureDecision,
    FreshIsolationAttestation,
    InternalRetryPolicy,
    Protocol,
    RetryAuthorization,
    Run,
)
from memrelay_eval.domain.errors import (
    InternalRetryLimitExceededError,
    InvalidAttemptTerminalError,
    RetryDeniedError,
)
from memrelay_eval.domain.ids import AssignmentId, AttemptId, ProtocolId, RunId
from memrelay_eval.domain.states import (
    AttemptTerminalKind,
    ExposureClassification,
    InternalRetrySubsystem,
    RetryRequestPurpose,
)
from memrelay_eval.orchestration.attempt import InternalRetryRecorder
from memrelay_eval.orchestration.retry import RetryAuthorizer


class _AuthorizationRaceLedger(InMemoryLedger):
    """Synchronizes callers before the atomic authorization reservation."""

    def __init__(self) -> None:
        super().__init__()
        self._append_barrier = Barrier(2)

    def append_retry_authorization_once(self, authorization: RetryAuthorization) -> bool:
        self._append_barrier.wait(timeout=2)
        return super().append_retry_authorization_once(authorization)


class _InternalRetryRaceLedger(InMemoryLedger):
    """Synchronizes callers before the atomic internal retry reservation."""

    def __init__(self) -> None:
        super().__init__()
        self._count_barrier = Barrier(2)

    def reserve_internal_retry(
        self,
        attempt_id: AttemptId,
        subsystem: InternalRetrySubsystem,
        maximum_retries: int,
    ) -> object:
        self._count_barrier.wait(timeout=2)
        return super().reserve_internal_retry(attempt_id, subsystem, maximum_retries)


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
    assert retry.parent_assignment_id == run.assignment_id
    assert retry.parent_terminal is terminal
    assert retry.parent_terminal.evidence_refs == terminal.evidence_refs
    assert retry.exposure_evidence_refs == exposure.evidence_refs
    assert retry.isolation_evidence_refs == isolation.evidence_refs


def test_retry_preserves_parent_evidence_bytes_and_hashes() -> None:
    protocol, run, attempt, terminal, exposure, isolation = _inputs()
    store = InMemoryArtifactStore()
    parent_bytes = b"native provisioning failure evidence"
    parent_ref = store.put_bytes(
        parent_bytes, media_type="application/octet-stream", classification="synthetic"
    )
    terminal = AttemptTerminal(
        attempt.id,
        run.id,
        terminal.classification,
        terminal.occurred_at,
        terminal.reason,
        (parent_ref,),
    )
    before_bytes = store.open_verified(parent_ref)
    before_sha256 = sha256(before_bytes).hexdigest()
    ledger = InMemoryLedger()
    ledger.append_attempt_terminal(terminal)

    retry = RetryAuthorizer(ledger).authorize(
        protocol, run, attempt, terminal, exposure=exposure, isolation=isolation
    )

    after_bytes = store.open_verified(parent_ref)
    assert after_bytes == before_bytes == parent_bytes
    assert sha256(after_bytes).hexdigest() == before_sha256 == parent_ref.sha256
    assert retry.parent_terminal.evidence_refs == (parent_ref,)


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
        (
            lambda protocol, terminal, exposure, isolation: FreshIsolationAttestation(
                False, isolation.evidence_refs
            ),
            "retry_fresh_isolation_unattested",
        ),
        (
            lambda protocol, terminal, exposure, isolation: FreshIsolationAttestation(True, ()),
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


def _assert_purpose_rejected(purpose: RetryRequestPurpose) -> None:
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


def test_best_of_n_is_rejected() -> None:
    _assert_purpose_rejected(RetryRequestPurpose.BEST_OF_N)


def test_repeated_success_is_rejected() -> None:
    _assert_purpose_rejected(RetryRequestPurpose.REPEAT_UNTIL_SUCCESS)


def test_favorable_later_attempt_is_rejected() -> None:
    _assert_purpose_rejected(RetryRequestPurpose.FAVORABLE_SUBSTITUTION)


def test_post_exposure_failure_is_rejected() -> None:
    protocol, run, attempt, terminal, exposure, isolation = _inputs()
    post_exposure = AttemptTerminal(
        attempt.id,
        run.id,
        AttemptTerminalKind.INFRASTRUCTURE_FAILED_POST_EXPOSURE,
        terminal.occurred_at,
        terminal.reason,
        terminal.evidence_refs,
    )
    ledger = InMemoryLedger()
    ledger.append_attempt_terminal(post_exposure)

    with pytest.raises(RetryDeniedError) as error:
        RetryAuthorizer(ledger).authorize(
            protocol, run, attempt, post_exposure, exposure=exposure, isolation=isolation
        )

    assert error.value.code == "retry_terminal_not_pre_exposure_infrastructure_failure"


def test_re_randomization_is_rejected_and_retry_inherits_parent_assignment() -> None:
    protocol, run, attempt, terminal, exposure, isolation = _inputs()
    ledger = InMemoryLedger()
    ledger.append_attempt_terminal(terminal)
    retry = RetryAuthorizer(ledger).authorize(
        protocol, run, attempt, terminal, exposure=exposure, isolation=isolation
    )

    assert retry.assignment_id == retry.parent_assignment_id == run.assignment_id
    with pytest.raises(InvalidAttemptTerminalError):
        RetryAuthorization(
            run_id=run.id,
            assignment_id=AssignmentId.new(),
            parent_assignment_id=run.assignment_id,
            parent_attempt_id=attempt.id,
            attempt=Attempt(AttemptId.new(), run.id),
            parent_terminal=terminal,
            exposure_evidence_refs=exposure.evidence_refs,
            isolation_evidence_refs=isolation.evidence_refs,
        )


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


def test_internal_retry_budgets_are_independent_for_all_subsystems() -> None:
    attempt_id = AttemptId.new()
    policies = tuple(
        InternalRetryPolicy(subsystem, maximum_retries=1) for subsystem in InternalRetrySubsystem
    )
    ledger = InMemoryLedger()
    recorder = InternalRetryRecorder(attempt_id, policies, ledger, InMemoryTelemetry())

    records = tuple(recorder.record(subsystem) for subsystem in InternalRetrySubsystem)

    assert tuple(record.subsystem for record in records) == tuple(InternalRetrySubsystem)
    assert all(record.retry_number == 1 for record in records)
    for subsystem in InternalRetrySubsystem:
        with pytest.raises(InternalRetryLimitExceededError):
            recorder.record(subsystem)
