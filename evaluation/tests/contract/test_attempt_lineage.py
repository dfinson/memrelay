from __future__ import annotations

from datetime import UTC, datetime

import pytest
from memrelay_eval.adapters.fakes import InMemoryLedger, InMemoryTelemetry
from memrelay_eval.domain.entities import (
    ArtifactRef,
    Attempt,
    AttemptTerminal,
    ExposureDecision,
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
)
from memrelay_eval.orchestration.attempt import InternalRetryRecorder
from memrelay_eval.orchestration.retry import RetryAuthorizer


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
        "provisioning failure",
        (ArtifactRef.from_bytes(b"provisioning failure"),),
    )
    return (
        Protocol(ProtocolId.new(), allows_pre_exposure_infrastructure_retry=True),
        run,
        attempt,
        terminal,
        ExposureDecision(
            ExposureClassification.UNEXPOSED,
            (ArtifactRef.from_bytes(b"unexposed"),),
        ),
        FreshIsolationAttestation(True, (ArtifactRef.from_bytes(b"fresh isolation"),)),
    )


def test_retry_of_retry_is_rejected_for_the_same_run() -> None:
    protocol, run, attempt, terminal, exposure, isolation = _inputs()
    ledger = InMemoryLedger()
    ledger.append_attempt_terminal(terminal)
    authorizer = RetryAuthorizer(ledger)
    authorizer.authorize(protocol, run, attempt, terminal, exposure=exposure, isolation=isolation)

    with pytest.raises(RetryDeniedError) as error:
        RetryAuthorizer(ledger).authorize(
            protocol, run, attempt, terminal, exposure=exposure, isolation=isolation
        )

    assert error.value.code == "retry_already_authorized_for_run"
    assert len(ledger.retry_authorizations) == 1


def test_internal_retries_are_bounded_and_recorded_per_subsystem() -> None:
    policy = InternalRetryPolicy(InternalRetrySubsystem.INSPECT, maximum_retries=1)
    ledger = InMemoryLedger()
    telemetry = InMemoryTelemetry()
    attempt_id = AttemptId.new()
    recorder = InternalRetryRecorder(attempt_id, (policy,), ledger, telemetry)

    first = recorder.record(InternalRetrySubsystem.INSPECT)
    assert first.retry_number == 1
    assert recorder.records == (first,)
    assert ledger.internal_retries == (first,)
    assert telemetry.observations[-1].event_name == "internal_retry"
    assert dict(telemetry.observations[-1].attributes) == {"retry_number": 1}

    with pytest.raises(InternalRetryLimitExceededError) as error:
        InternalRetryRecorder(attempt_id, (policy,), ledger, telemetry).record(
            InternalRetrySubsystem.INSPECT
        )

    assert error.value.code == "internal_retry_limit_exceeded"
