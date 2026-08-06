from __future__ import annotations

from datetime import UTC, datetime

import pytest
from memrelay_eval.domain.entities import ArtifactRef, Attempt, AttemptTerminal, RetryAuthorization
from memrelay_eval.domain.ids import (
    AssignmentId,
    AttemptId,
    ExperimentId,
    IntentId,
    ProtocolId,
    RunId,
)
from memrelay_eval.domain.intents import (
    AttemptTerminalIntent,
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    IntentAck,
    IntentMetadata,
    RunTransitionIntent,
)
from memrelay_eval.domain.states import AttemptTerminalKind, RunState
from memrelay_eval.ledger.repository import LedgerFaultInjectedError, SqliteLedger

NOW = datetime(2026, 8, 5, 21, 30, tzinfo=UTC)


def meta(**kwargs: object) -> IntentMetadata:
    return IntentMetadata(IntentId.new(), NOW, reason_code="control_recorded", **kwargs)


def assert_ack(value: object) -> IntentAck:
    assert isinstance(value, IntentAck)
    return value


def seed(ledger: SqliteLedger) -> tuple[RunId, AttemptId, AssignmentId]:
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    assert_ack(
        ledger.submit_intent(CreateExperimentIntent(meta(), experiment_id, ProtocolId.new()))
    )
    assignment_id = AssignmentId.new()
    assert_ack(ledger.submit_intent(CreateRunIntent(meta(), run_id, experiment_id, assignment_id)))
    assert_ack(ledger.submit_intent(CreateAttemptIntent(meta(), attempt_id, run_id)))
    return run_id, attempt_id, assignment_id


@pytest.mark.parametrize("boundary", ("before_begin", "after_append", "before_commit"))
def test_precommit_faults_leave_no_partial_intent(tmp_path: object, boundary: str) -> None:
    target_id = IntentId.new()

    def fault(point: str) -> None:
        if point == boundary:
            raise LedgerFaultInjectedError(point)

    ledger = SqliteLedger.open_control(tmp_path / f"{boundary}.sqlite", fault_injector=fault)  # type: ignore[operator]
    with pytest.raises(LedgerFaultInjectedError):
        ledger.submit_intent(
            CreateExperimentIntent(
                IntentMetadata(target_id, NOW, reason_code="control_recorded"),
                ExperimentId.new(),
                ProtocolId.new(),
            )
        )
    ledger.close()
    reopened = SqliteLedger.open_control(tmp_path / f"{boundary}.sqlite")  # type: ignore[operator]
    assert reopened.logical_history() == ()
    reopened.close()


def test_commit_before_ack_crash_reopens_exact_history_and_replays_once(tmp_path: object) -> None:
    armed = False

    def fault(point: str) -> None:
        if armed and point == "after_commit_before_ack":
            raise LedgerFaultInjectedError(point)

    path = tmp_path / "ack-loss.sqlite"  # type: ignore[operator]
    ledger = SqliteLedger.open_control(path, fault_injector=fault)
    run_id, attempt_id, assignment_id = seed(ledger)
    retry_attempt_id = AttemptId.new()
    pre_exposure_evidence = ArtifactRef.from_bytes(b"pre-exposure infrastructure evidence")
    assert_ack(
        ledger.submit_intent(
            AttemptTerminalIntent(
                IntentMetadata(
                    IntentId.new(),
                    NOW,
                    source_attempt_id=attempt_id,
                    reason_code="infrastructure_pre_exposure",
                    evidence_refs=(pre_exposure_evidence,),
                ),
                attempt_id,
                run_id,
                AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
                pre_exposure_evidence,
            )
        )
    )
    terminal = ledger.attempt_terminal_for(attempt_id)
    assert terminal is not None
    authorization = RetryAuthorization(
        run_id,
        assignment_id,
        assignment_id,
        attempt_id,
        Attempt(retry_attempt_id, run_id),
        terminal,
        (ArtifactRef.from_bytes(b"unexposed"),),
        (ArtifactRef.from_bytes(b"fresh isolation"),),
    )
    assert ledger.append_retry_authorization_once(authorization) is True
    intent = RunTransitionIntent(
        IntentMetadata(
            IntentId.new(),
            NOW,
            source_attempt_id=attempt_id,
            expected_prior_state=RunState.PLANNED,
            expected_prior_digest=None,
            monotonic_ns=1,
            reason_code="lifecycle_advanced",
        ),
        run_id,
        RunState.PLANNED,
        RunState.ASSIGNED,
    )
    before = ledger.canonical_history()
    armed = True
    with pytest.raises(LedgerFaultInjectedError):
        ledger.submit_intent(intent)
    ledger.close()

    reopened = SqliteLedger.open_control(path)
    after_reopen = reopened.canonical_history()
    assert after_reopen != before
    replay = assert_ack(reopened.submit_intent(intent))
    assert replay.idempotent is True
    assert reopened.canonical_history() == after_reopen
    assert [item.next_state for item in reopened.history(run_id)] == [RunState.ASSIGNED]
    assert reopened.retry_lineage(run_id) == ((attempt_id, retry_attempt_id),)
    reopened.checkpoint()
    assert reopened.integrity_check() == "ok"
    reopened.close()


@pytest.mark.parametrize("boundary", ("before_wal_checkpoint", "after_wal_checkpoint"))
def test_checkpoint_fault_preserves_committed_history(tmp_path: object, boundary: str) -> None:
    def fault(point: str) -> None:
        if point == boundary:
            raise LedgerFaultInjectedError(point)

    path = tmp_path / f"{boundary}.sqlite"  # type: ignore[operator]
    ledger = SqliteLedger.open_control(path, fault_injector=fault)
    seed(ledger)
    before = ledger.canonical_history()
    with pytest.raises(LedgerFaultInjectedError):
        ledger.checkpoint()
    ledger.close()

    reopened = SqliteLedger.open_control(path)
    assert reopened.canonical_history() == before
    assert reopened.integrity_check() == "ok"
    reopened.close()


def test_retry_authorization_precommit_fault_rolls_back_and_replays_once(tmp_path: object) -> None:
    armed = False

    def fault(point: str) -> None:
        if armed and point == "before_retry_authorization_commit":
            raise LedgerFaultInjectedError(point)

    path = tmp_path / "retry-authorization.sqlite"  # type: ignore[operator]
    ledger = SqliteLedger.open_control(path, fault_injector=fault)
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    assignment_id = AssignmentId.new()
    assert_ack(
        ledger.submit_intent(CreateExperimentIntent(meta(), experiment_id, ProtocolId.new()))
    )
    assert_ack(ledger.submit_intent(CreateRunIntent(meta(), run_id, experiment_id, assignment_id)))
    assert_ack(ledger.submit_intent(CreateAttemptIntent(meta(), attempt_id, run_id)))
    terminal = AttemptTerminal(
        attempt_id,
        run_id,
        AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
        NOW,
        "provisioning failure",
        (ArtifactRef.from_bytes(b"terminal"),),
    )
    ledger.append_attempt_terminal(terminal)
    authorization = RetryAuthorization(
        run_id,
        assignment_id,
        assignment_id,
        attempt_id,
        Attempt(AttemptId.new(), run_id),
        terminal,
        (ArtifactRef.from_bytes(b"unexposed"),),
        (ArtifactRef.from_bytes(b"fresh isolation"),),
    )
    armed = True
    with pytest.raises(LedgerFaultInjectedError):
        ledger.append_retry_authorization_once(authorization)
    ledger.close()

    reopened = SqliteLedger.open_control(path)
    assert reopened.retry_authorizations_for(run_id) == ()
    assert reopened.append_retry_authorization_once(authorization) is True
    assert reopened.append_retry_authorization_once(authorization) is False
    assert reopened.retry_authorizations_for(run_id) == (authorization,)
    assert reopened.integrity_check() == "ok"
    reopened.close()
