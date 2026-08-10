from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from multiprocessing import get_context
from threading import Thread

import pytest
from memrelay_eval.adapters.fakes import InMemoryLedger, InMemoryTelemetry
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import (
    ArtifactLink,
    ArtifactRef,
    Attempt,
    AttemptTerminal,
    ExposureDecision,
    FreshIsolationAttestation,
    InclusionDecision,
    InternalRetryPolicy,
    Protocol,
    RetryAuthorization,
    Run,
)
from memrelay_eval.domain.errors import LedgerIntentConflictError, LedgerOwnershipError
from memrelay_eval.domain.ids import (
    AssignmentId,
    AttemptId,
    ExperimentId,
    InclusionId,
    IntentId,
    ProtocolId,
    RunId,
)
from memrelay_eval.domain.intents import (
    ArtifactLinkIntent,
    AttemptTerminalIntent,
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    InclusionDecisionIntent,
    IntentAck,
    IntentMetadata,
    IntentRejection,
    RetryLineageIntent,
    RunTransitionIntent,
)
from memrelay_eval.domain.states import (
    AttemptTerminalKind,
    ExposureClassification,
    InclusionStatus,
    InternalRetrySubsystem,
    RunState,
)
from memrelay_eval.ledger import SqliteLedger
from memrelay_eval.ledger.schema import MIGRATIONS
from memrelay_eval.orchestration.attempt import AttemptTerminalRecorder, InternalRetryRecorder
from memrelay_eval.orchestration.control import LedgerControl
from memrelay_eval.orchestration.retry import RetryAuthorizer
from memrelay_eval.orchestration.worker import WorkerIntentEmitter

NOW = datetime(2026, 8, 5, 21, 0, tzinfo=UTC)
PROCESS_TIMEOUT_SECONDS = 45


def _try_acquire_ledger(path: str, result: object) -> None:
    try:
        ledger = SqliteLedger.open_control(path)
    except LedgerOwnershipError:
        result.put("blocked")  # type: ignore[union-attr]
    else:
        ledger.close()
        result.put("acquired")  # type: ignore[union-attr]


def _hold_ledger_until_terminated(path: str, ready: object, release: object) -> None:
    ledger = SqliteLedger.open_control(path)
    ready.put("owned")  # type: ignore[union-attr]
    release.wait()  # type: ignore[union-attr]
    ledger.close()


def _race_to_acquire_ledger(path: str, result: object, release: object) -> None:
    try:
        ledger = SqliteLedger.open_control(path)
    except LedgerOwnershipError:
        result.put("blocked")  # type: ignore[union-attr]
    else:
        result.put("acquired")  # type: ignore[union-attr]
        release.wait()  # type: ignore[union-attr]
        ledger.close()


def metadata(
    *,
    source_attempt_id: AttemptId | None = None,
    expected_prior_state: RunState | None = None,
    expected_prior_digest: str | None = None,
    monotonic_ns: int | None = None,
    reason_code: str = "control_recorded",
    safe_metadata: dict[str, bool | int | float] | None = None,
    evidence_refs: tuple[ArtifactRef, ...] = (),
) -> IntentMetadata:
    return IntentMetadata(
        IntentId.new(),
        NOW,
        source_attempt_id=source_attempt_id,
        expected_prior_state=expected_prior_state,
        expected_prior_digest=expected_prior_digest,
        monotonic_ns=monotonic_ns,
        evidence_refs=evidence_refs,
        reason_code=reason_code,
        safe_metadata={} if safe_metadata is None else safe_metadata,
    )


def accepted(result: IntentAck | IntentRejection) -> IntentAck:
    assert isinstance(result, IntentAck)
    return result


def seed(ledger: SqliteLedger) -> tuple[ExperimentId, RunId, AttemptId]:
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    accepted(
        ledger.submit_intent(CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new()))
    )
    accepted(
        ledger.submit_intent(CreateRunIntent(metadata(), run_id, experiment_id, AssignmentId.new()))
    )
    accepted(ledger.submit_intent(CreateAttemptIntent(metadata(), attempt_id, run_id)))
    return experiment_id, run_id, attempt_id


def test_ledger_intent_and_history_digests_use_shared_canonical_bytes(tmp_path: object) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    intent = CreateExperimentIntent(
        metadata(safe_metadata={"score": 1.0}),
        ExperimentId.new(),
        ProtocolId.new(),
    )
    expected_intent_digest = sha256(canonical_bytes(intent.to_payload())).hexdigest()

    result = accepted(ledger.submit_intent(intent))
    assert result.canonical_payload_digest == expected_intent_digest

    event = ledger.logical_history()
    expected_history = canonical_bytes(
        {
            "events": [
                {
                    "sequence": item.sequence,
                    "intent_id": str(item.intent_id),
                    "payload_digest": item.canonical_payload_digest,
                    "kind": item.kind.value,
                    "occurred_at": item.occurred_at.isoformat(timespec="microseconds").replace(
                        "+00:00", "Z"
                    ),
                }
                for item in event
            ],
            "rejections": [],
        }
    )
    assert ledger.canonical_history() == expected_history
    ledger.close()


def transition(
    ledger: SqliteLedger,
    run_id: RunId,
    source_attempt_id: AttemptId,
    previous: RunState,
    next_state: RunState,
    digest: str | None,
    *,
    monotonic_ns: int,
) -> IntentAck:
    return accepted(
        ledger.submit_intent(
            RunTransitionIntent(
                metadata(
                    source_attempt_id=source_attempt_id,
                    expected_prior_state=previous,
                    expected_prior_digest=digest,
                    monotonic_ns=monotonic_ns,
                    reason_code="lifecycle_advanced",
                ),
                run_id,
                previous,
                next_state,
            )
        )
    )


def test_sqlite_ledger_records_normalized_append_only_lifecycle_and_refs(tmp_path: object) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    experiment_id, run_id, attempt_id = seed(ledger)
    reference = ArtifactRef.from_bytes(b"immutable evidence")

    artifact_ack = accepted(
        ledger.submit_intent(
            ArtifactLinkIntent(
                metadata(
                    source_attempt_id=attempt_id,
                    reason_code="artifact_recorded",
                    evidence_refs=(reference,),
                ),
                ArtifactLink(
                    reference,
                    "inspect_export",
                    experiment_id=experiment_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                ),
            )
        )
    )
    digest: str | None = None
    state = RunState.PLANNED
    for monotonic_ns, next_state in enumerate(
        (
            RunState.ASSIGNED,
            RunState.PROVISIONED,
            RunState.RUNNING,
            RunState.EXPORTED,
            RunState.SCORED,
            RunState.RECONCILED,
        ),
        start=1,
    ):
        transition_ack = transition(
            ledger,
            run_id,
            attempt_id,
            state,
            next_state,
            digest,
            monotonic_ns=monotonic_ns,
        )
        digest = transition_ack.canonical_payload_digest
        state = next_state

    decision = InclusionDecision(
        InclusionId.new(),
        run_id,
        InclusionStatus.INCLUDED,
        "reconciliation_complete",
        "a" * 64,
        NOW,
    )
    accepted(
        ledger.submit_intent(
            InclusionDecisionIntent(
                metadata(
                    source_attempt_id=attempt_id,
                    expected_prior_state=RunState.RECONCILED,
                    expected_prior_digest=digest,
                    monotonic_ns=7,
                    reason_code="reconciliation_complete",
                ),
                decision,
            )
        )
    )
    transition(
        ledger,
        run_id,
        attempt_id,
        RunState.RECONCILED,
        RunState.INCLUDED,
        digest,
        monotonic_ns=8,
    )

    assert artifact_ack.idempotent is False
    assert [item.next_state for item in ledger.history(run_id)] == [
        RunState.ASSIGNED,
        RunState.PROVISIONED,
        RunState.RUNNING,
        RunState.EXPORTED,
        RunState.SCORED,
        RunState.RECONCILED,
        RunState.INCLUDED,
    ]
    assert ledger.sqlite_settings() == {"journal_mode": "wal", "foreign_keys": True}
    assert ledger.integrity_check() == "ok"
    assert ledger.schema_version == 5
    assert ledger.migration_journal == tuple(
        (migration.version, migration.digest) for migration in MIGRATIONS
    )
    assert len(ledger.logical_history()) == 12
    ledger.close()


def test_only_one_control_connection_can_own_a_ledger_path(tmp_path: object) -> None:
    path = tmp_path / "ledger.sqlite"  # type: ignore[operator]
    first = SqliteLedger.open_control(path)
    with pytest.raises(LedgerOwnershipError):
        SqliteLedger.open_control(path)
    first.close()
    reopened = SqliteLedger.open_control(path)
    reopened.close()


def test_attempt_execution_claim_is_atomic_and_terminal_attempts_cannot_replay(
    tmp_path: object,
) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    _, run_id, attempt_id = seed(ledger)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = tuple(
            executor.map(
                lambda _: ledger.claim_attempt_execution(attempt_id, run_id),
                range(2),
            )
        )

    assert claims.count(True) == 1
    ledger.append_attempt_terminal(
        AttemptTerminal(
            attempt_id,
            run_id,
            AttemptTerminalKind.SUCCEEDED,
            NOW,
            "completed",
        )
    )
    assert ledger.claim_attempt_execution(attempt_id, run_id) is False
    ledger.close()


def test_control_ownership_is_exclusive_across_processes_and_released_on_close(
    tmp_path: object,
) -> None:
    path = tmp_path / "ledger.sqlite"  # type: ignore[operator]
    owner = SqliteLedger.open_control(path)
    context = get_context("spawn")
    result = context.Queue()
    contender = context.Process(target=_try_acquire_ledger, args=(str(path), result))
    contender.start()
    assert result.get(timeout=PROCESS_TIMEOUT_SECONDS) == "blocked"
    contender.join(timeout=PROCESS_TIMEOUT_SECONDS)
    assert contender.exitcode == 0

    owner.close()
    successor_result = context.Queue()
    successor = context.Process(
        target=_try_acquire_ledger,
        args=(str(path), successor_result),
    )
    successor.start()
    assert successor_result.get(timeout=PROCESS_TIMEOUT_SECONDS) == "acquired"
    successor.join(timeout=PROCESS_TIMEOUT_SECONDS)
    assert successor.exitcode == 0


def test_control_ownership_is_released_when_owner_process_crashes(tmp_path: object) -> None:
    path = tmp_path / "ledger.sqlite"  # type: ignore[operator]
    context = get_context("spawn")
    ready = context.Queue()
    release = context.Event()
    owner = context.Process(
        target=_hold_ledger_until_terminated,
        args=(str(path), ready, release),
    )
    owner.start()
    assert ready.get(timeout=PROCESS_TIMEOUT_SECONDS) == "owned"
    owner.terminate()
    owner.join(timeout=15)
    assert owner.exitcode is not None

    recovered = SqliteLedger.open_control(path)
    recovered.close()


def test_control_ownership_race_has_exactly_one_process_winner(tmp_path: object) -> None:
    path = tmp_path / "ledger.sqlite"  # type: ignore[operator]
    context = get_context("spawn")
    result = context.Queue()
    release = context.Event()
    contenders = tuple(
        context.Process(target=_race_to_acquire_ledger, args=(str(path), result, release))
        for _ in range(2)
    )
    for contender in contenders:
        contender.start()
    outcomes = sorted(result.get(timeout=PROCESS_TIMEOUT_SECONDS) for _ in contenders)
    assert outcomes == ["acquired", "blocked"]
    release.set()
    for contender in contenders:
        contender.join(timeout=PROCESS_TIMEOUT_SECONDS)
        assert contender.exitcode == 0


@pytest.mark.parametrize(
    ("safe_metadata", "occurred_at", "expected_reason"),
    [
        pytest.param({key: 1}, NOW, "thin_ledger_violation", id=f"blocked-{key}")
        for key in (
            "prompt",
            "patch",
            "trace",
            "grader",
            "inspect",
            "provider",
            "credential",
            "repository",
            "repo",
            "payload",
            "event",
            "body",
            "secret",
            "token",
            "password",
            "code",
            "treatment",
            "arm",
            "condition",
        )
    ]
    + [
        ({"treat_ment": 1}, NOW, "thin_ledger_violation"),
        ({"trial_arm": 1}, NOW, "thin_ledger_violation"),
        ({}, datetime(2026, 8, 5, 21, 0), "non_utc_timestamp"),
        (
            {},
            datetime(2026, 8, 5, 22, 0, tzinfo=timezone(timedelta(hours=1))),
            "non_utc_timestamp",
        ),
    ],
)
def test_fake_and_sqlite_share_intent_preflight_rejections(
    tmp_path: object,
    safe_metadata: dict[str, int],
    occurred_at: datetime,
    expected_reason: str,
) -> None:
    intent = CreateExperimentIntent(
        IntentMetadata(
            IntentId.new(),
            occurred_at,
            reason_code="control_recorded",
            safe_metadata=safe_metadata,
        ),
        ExperimentId.new(),
        ProtocolId.new(),
    )
    durable = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    try:
        outcomes = (InMemoryLedger().submit_intent(intent), durable.submit_intent(intent))
    finally:
        durable.close()
    assert all(isinstance(outcome, IntentRejection) for outcome in outcomes)
    assert [outcome.reason_code for outcome in outcomes] == [expected_reason, expected_reason]  # type: ignore[union-attr]


def test_shared_preflight_applies_to_every_intent_scope_and_preserves_safe_keys(
    tmp_path: object,
) -> None:
    reference = ArtifactRef.from_bytes(b"reference")

    def unsafe_metadata() -> IntentMetadata:
        return IntentMetadata(
            IntentId.new(),
            NOW,
            reason_code="control_recorded",
            safe_metadata={"trial_arm": 1},
        )

    intents = (
        CreateExperimentIntent(unsafe_metadata(), ExperimentId.new(), ProtocolId.new()),
        CreateRunIntent(unsafe_metadata(), RunId.new(), ExperimentId.new(), AssignmentId.new()),
        CreateAttemptIntent(unsafe_metadata(), AttemptId.new(), RunId.new()),
        RunTransitionIntent(
            unsafe_metadata(),
            RunId.new(),
            RunState.PLANNED,
            RunState.ASSIGNED,
        ),
        AttemptTerminalIntent(
            unsafe_metadata(),
            AttemptId.new(),
            RunId.new(),
            AttemptTerminalKind.PROVIDER_UNAVAILABLE,
        ),
        ArtifactLinkIntent(
            unsafe_metadata(),
            ArtifactLink(reference, "artifact_recorded", experiment_id=ExperimentId.new()),
        ),
        RetryLineageIntent(unsafe_metadata(), RunId.new(), AttemptId.new(), AttemptId.new()),
        InclusionDecisionIntent(
            unsafe_metadata(),
            InclusionDecision(
                InclusionId.new(),
                RunId.new(),
                InclusionStatus.EXCLUDED,
                "reconciliation_complete",
                "a" * 64,
                NOW,
            ),
        ),
    )
    durable = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    try:
        for intent in intents:
            fake_result = InMemoryLedger().submit_intent(intent)
            sqlite_result = durable.submit_intent(intent)
            assert isinstance(fake_result, IntentRejection)
            assert isinstance(sqlite_result, IntentRejection)
            assert fake_result.reason_code == sqlite_result.reason_code == "thin_ledger_violation"

        accepted_metadata = CreateExperimentIntent(
            IntentMetadata(
                IntentId.new(),
                NOW,
                reason_code="control_recorded",
                safe_metadata={"assignment_id": 1},
            ),
            ExperimentId.new(),
            ProtocolId.new(),
        )
        assert isinstance(InMemoryLedger().submit_intent(accepted_metadata), IntentAck)
        assert isinstance(durable.submit_intent(accepted_metadata), IntentAck)
    finally:
        durable.close()


def test_sqlite_retry_operations_are_atomic_and_recoverable(tmp_path: object) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    assignment_id = AssignmentId.new()
    accepted(
        ledger.submit_intent(CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new()))
    )
    accepted(
        ledger.submit_intent(CreateRunIntent(metadata(), run_id, experiment_id, assignment_id))
    )
    accepted(ledger.submit_intent(CreateAttemptIntent(metadata(), attempt_id, run_id)))
    terminal = AttemptTerminal(
        attempt_id,
        run_id,
        AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
        NOW,
        "provisioning failure",
        (ArtifactRef.from_bytes(b"terminal"),),
    )
    ledger.append_attempt_terminal(terminal)
    assert ledger.attempt_terminal_for(attempt_id) == terminal
    authorization = RetryAuthorization(
        run_id,
        assignment_id,
        assignment_id,
        attempt_id,
        Attempt(AttemptId.new(), run_id),
        terminal,
        (ArtifactRef.from_bytes(b"unexposed"),),
        (ArtifactRef.from_bytes(b"fresh"),),
    )
    outcomes: list[bool] = []
    threads = tuple(
        Thread(
            target=lambda: outcomes.append(ledger.append_retry_authorization_once(authorization))
        )
        for _ in range(2)
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == [False, True]
    assert ledger.retry_authorizations_for(run_id) == (authorization,)

    retry_outcomes: list[object] = []
    threads = tuple(
        Thread(
            target=lambda: retry_outcomes.append(
                ledger.reserve_internal_retry(attempt_id, InternalRetrySubsystem.INSPECT, 1)
            )
        )
        for _ in range(2)
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(item is not None for item in retry_outcomes) == 1
    internal_retries = ledger.internal_retries_for(attempt_id, InternalRetrySubsystem.INSPECT)
    assert internal_retries[0].retry_number == 1
    ledger.close()

    reopened = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    assert reopened.retry_authorizations_for(run_id) == (authorization,)
    assert reopened.reserve_internal_retry(attempt_id, InternalRetrySubsystem.INSPECT, 1) is None
    reopened.close()


def test_story_17_retry_recorders_operate_against_sqlite_ledger(tmp_path: object) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    assignment_id = AssignmentId.new()
    accepted(
        ledger.submit_intent(CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new()))
    )
    accepted(
        ledger.submit_intent(CreateRunIntent(metadata(), run_id, experiment_id, assignment_id))
    )
    accepted(ledger.submit_intent(CreateAttemptIntent(metadata(), attempt_id, run_id)))
    terminal = AttemptTerminal(
        attempt_id,
        run_id,
        AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
        NOW,
        "provisioning failure",
        (ArtifactRef.from_bytes(b"terminal"),),
    )
    telemetry = InMemoryTelemetry()
    AttemptTerminalRecorder(ledger, telemetry).append(terminal)
    authorization = RetryAuthorizer(ledger).authorize(
        Protocol(ProtocolId.new(), allows_pre_exposure_infrastructure_retry=True),
        Run(run_id, assignment_id),
        Attempt(attempt_id, run_id),
        terminal,
        exposure=ExposureDecision(
            ExposureClassification.UNEXPOSED,
            (ArtifactRef.from_bytes(b"unexposed"),),
        ),
        isolation=FreshIsolationAttestation(True, (ArtifactRef.from_bytes(b"fresh isolation"),)),
    )
    record = InternalRetryRecorder(
        attempt_id,
        (InternalRetryPolicy(InternalRetrySubsystem.SDK, maximum_retries=1),),
        ledger,
        telemetry,
    ).record(InternalRetrySubsystem.SDK)
    assert ledger.retry_authorizations_for(run_id) == (authorization,)
    assert ledger.attempt_terminal_for(attempt_id) == terminal
    assert ledger.internal_retries_for(attempt_id, InternalRetrySubsystem.SDK) == (record,)
    ledger.close()


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "fork"),
    reason="forked descriptor inheritance is POSIX-specific",
)
def test_forked_child_closes_inherited_ledger_handles_before_opening_control_lease(
    tmp_path: object,
) -> None:
    path = tmp_path / "ledger.sqlite"  # type: ignore[operator]
    parent_ledger = SqliteLedger.open_control(path)
    ready_read, ready_write = os.pipe()
    continue_read, continue_write = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.close(ready_read)
            os.close(continue_write)
            os.write(ready_write, b"1")
            os.read(continue_read, 1)
            child_ledger = SqliteLedger.open_control(path)
            child_ledger.close()
        except BaseException:
            os._exit(1)
        else:
            os._exit(0)
    os.close(ready_write)
    os.close(continue_read)
    try:
        assert os.read(ready_read, 1) == b"1"
        parent_ledger.close()
        os.write(continue_write, b"1")
        _, status = os.waitpid(child_pid, 0)
        assert os.WIFEXITED(status)
        assert os.WEXITSTATUS(status) == 0
    finally:
        os.close(ready_read)
        os.close(continue_write)


def test_worker_emitter_routes_to_control_and_extended_fake_preserves_unpaid_inclusion() -> None:
    ledger = InMemoryLedger()
    control = LedgerControl(ledger)
    worker = WorkerIntentEmitter(control)
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    denied_experiment = worker.emit(
        CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new())
    )
    assert isinstance(denied_experiment, IntentRejection)
    assert denied_experiment.reason_code == "identity_creation_control_only"
    accepted(
        control.create_experiment(
            CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new())
        )
    )
    accepted(
        control.create_run(CreateRunIntent(metadata(), run_id, experiment_id, AssignmentId.new()))
    )
    accepted(control.create_initial_attempt(CreateAttemptIntent(metadata(), attempt_id, run_id)))
    denied_attempt = worker.emit(CreateAttemptIntent(metadata(), AttemptId.new(), run_id))
    assert isinstance(denied_attempt, IntentRejection)
    assert denied_attempt.reason_code == "attempt_creation_control_only"
    denied_retry = worker.emit(
        RetryLineageIntent(
            metadata(source_attempt_id=attempt_id),
            run_id,
            attempt_id,
            AttemptId.new(),
        )
    )
    assert isinstance(denied_retry, IntentRejection)
    assert denied_retry.reason_code == "attempt_creation_control_only"

    digest: str | None = None
    state = RunState.PLANNED
    for next_state in (
        RunState.ASSIGNED,
        RunState.PROVISIONED,
        RunState.RUNNING,
        RunState.EXPORTED,
        RunState.SCORED,
        RunState.RECONCILED,
    ):
        result = accepted(
            worker.emit(
                RunTransitionIntent(
                    metadata(
                        source_attempt_id=attempt_id,
                        expected_prior_state=state,
                        expected_prior_digest=digest,
                        reason_code="lifecycle_advanced",
                    ),
                    run_id,
                    state,
                    next_state,
                )
            )
        )
        digest = result.canonical_payload_digest
        state = next_state

    denied_inclusion = worker.emit(
        InclusionDecisionIntent(
            metadata(
                source_attempt_id=attempt_id,
                expected_prior_state=RunState.RECONCILED,
                expected_prior_digest=digest,
                reason_code="reconciliation_complete",
            ),
            InclusionDecision(
                InclusionId.new(),
                run_id,
                InclusionStatus.INCLUDED,
                "reconciliation_complete",
                "a" * 64,
                NOW,
            ),
        )
    )
    assert isinstance(denied_inclusion, IntentRejection)
    assert denied_inclusion.reason_code == "inclusion_control_only"
    unpaid = control.record_inclusion(
        InclusionDecisionIntent(
            metadata(
                source_attempt_id=attempt_id,
                expected_prior_state=RunState.RECONCILED,
                expected_prior_digest=digest,
                reason_code="reconciliation_complete",
            ),
            InclusionDecision(
                InclusionId.new(),
                run_id,
                InclusionStatus.INCLUDED,
                "reconciliation_complete",
                "a" * 64,
                NOW,
            ),
        )
    )
    assert isinstance(unpaid, IntentRejection)
    assert unpaid.reason_code == "unpaid_inclusion_forbidden"


def test_worker_emitter_routes_to_control_and_sqlite(tmp_path: object) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    control = LedgerControl(ledger)
    worker = WorkerIntentEmitter(control)
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    denied_experiment = worker.emit(
        CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new())
    )
    assert isinstance(denied_experiment, IntentRejection)
    assert denied_experiment.reason_code == "identity_creation_control_only"
    accepted(
        control.create_experiment(
            CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new())
        )
    )
    accepted(
        control.create_run(CreateRunIntent(metadata(), run_id, experiment_id, AssignmentId.new()))
    )
    accepted(control.create_initial_attempt(CreateAttemptIntent(metadata(), attempt_id, run_id)))
    prohibited_attempt = CreateAttemptIntent(metadata(), AttemptId.new(), run_id)
    denied_attempt = worker.emit(prohibited_attempt)
    assert isinstance(denied_attempt, IntentRejection)
    assert denied_attempt.reason_code == "attempt_creation_control_only"
    replayed_denial = worker.emit(prohibited_attempt)
    assert isinstance(replayed_denial, IntentRejection)
    assert replayed_denial.idempotent is True
    with pytest.raises(LedgerIntentConflictError):
        worker.emit(replace(prohibited_attempt, attempt_id=AttemptId.new()))

    transition_ack = accepted(
        worker.emit(
            RunTransitionIntent(
                metadata(
                    source_attempt_id=attempt_id,
                    expected_prior_state=RunState.PLANNED,
                    expected_prior_digest=None,
                    reason_code="lifecycle_advanced",
                ),
                run_id,
                RunState.PLANNED,
                RunState.ASSIGNED,
            )
        )
    )
    assert transition_ack.kind.value == "run_transition"
    assert [item.next_state for item in ledger.history(run_id)] == [RunState.ASSIGNED]
    ledger.close()


def test_worker_authority_is_attempt_scoped_and_ledger_rejects_fabricated_run(
    tmp_path: object,
) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    control = LedgerControl(ledger)
    worker = WorkerIntentEmitter(control)
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    accepted(
        control.create_experiment(
            CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new())
        )
    )
    accepted(
        control.create_run(CreateRunIntent(metadata(), run_id, experiment_id, AssignmentId.new()))
    )
    accepted(control.create_initial_attempt(CreateAttemptIntent(metadata(), attempt_id, run_id)))

    missing_source = worker.emit(
        RunTransitionIntent(
            metadata(
                expected_prior_state=RunState.PLANNED,
                expected_prior_digest=None,
                reason_code="lifecycle_advanced",
            ),
            run_id,
            RunState.PLANNED,
            RunState.ASSIGNED,
        )
    )
    assert isinstance(missing_source, IntentRejection)
    assert missing_source.reason_code == "missing_worker_source_attempt"

    fabricated_run = worker.emit(
        RunTransitionIntent(
            metadata(
                source_attempt_id=attempt_id,
                expected_prior_state=RunState.PLANNED,
                expected_prior_digest=None,
                reason_code="lifecycle_advanced",
            ),
            RunId.new(),
            RunState.PLANNED,
            RunState.ASSIGNED,
        )
    )
    assert isinstance(fabricated_run, IntentRejection)
    assert fabricated_run.reason_code == "unknown_run"

    missing_source_artifact = worker.emit(
        ArtifactLinkIntent(
            metadata(reason_code="artifact_recorded"),
            ArtifactLink(
                ArtifactRef.from_bytes(b"worker artifact"),
                "worker_export",
                run_id=run_id,
                attempt_id=attempt_id,
            ),
        )
    )
    assert isinstance(missing_source_artifact, IntentRejection)
    assert missing_source_artifact.reason_code == "missing_worker_source_attempt"

    cross_attempt_terminal = worker.emit(
        AttemptTerminalIntent(
            metadata(
                source_attempt_id=attempt_id,
                reason_code="agent_failed",
            ),
            AttemptId.new(),
            run_id,
            AttemptTerminalKind.AGENT_FAILED,
        )
    )
    assert isinstance(cross_attempt_terminal, IntentRejection)
    assert cross_attempt_terminal.reason_code == "worker_attempt_scope_mismatch"
    ledger.close()


def _seed_intent_ledger(ledger: object) -> tuple[ExperimentId, ExperimentId, RunId, AttemptId]:
    experiment_id = ExperimentId.new()
    other_experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    accepted(
        ledger.submit_intent(CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new()))
    )  # type: ignore[union-attr]
    accepted(  # type: ignore[union-attr]
        ledger.submit_intent(
            CreateExperimentIntent(metadata(), other_experiment_id, ProtocolId.new())
        )
    )
    accepted(  # type: ignore[union-attr]
        ledger.submit_intent(CreateRunIntent(metadata(), run_id, experiment_id, AssignmentId.new()))
    )
    accepted(ledger.submit_intent(CreateAttemptIntent(metadata(), attempt_id, run_id)))  # type: ignore[union-attr]
    return experiment_id, other_experiment_id, run_id, attempt_id


@pytest.mark.parametrize("backend", ("fake", "sqlite"))
def test_fake_and_sqlite_share_retry_source_attempt_rejection(
    tmp_path: object, backend: str
) -> None:
    ledger = (
        InMemoryLedger()
        if backend == "fake"
        else SqliteLedger.open_control(
            tmp_path / "ledger.sqlite"  # type: ignore[operator]
        )
    )
    _, _, run_id, attempt_id = _seed_intent_ledger(ledger)
    evidence = ArtifactRef.from_bytes(b"pre-exposure evidence")
    accepted(
        ledger.submit_intent(  # type: ignore[union-attr]
            AttemptTerminalIntent(
                metadata(
                    source_attempt_id=attempt_id,
                    evidence_refs=(evidence,),
                    reason_code="infrastructure_pre_exposure",
                ),
                attempt_id,
                run_id,
                AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
                evidence,
            )
        )
    )
    result = ledger.submit_intent(  # type: ignore[union-attr]
        RetryLineageIntent(
            metadata(reason_code="authorized_retry"),
            run_id,
            attempt_id,
            AttemptId.new(),
        )
    )
    assert isinstance(result, IntentRejection)
    assert result.reason_code == "retry_authorization_control_only"
    if isinstance(ledger, SqliteLedger):
        ledger.close()


@pytest.mark.parametrize("backend", ("fake", "sqlite"))
def test_artifact_link_rejects_experiment_run_ownership_mismatch(
    tmp_path: object, backend: str
) -> None:
    ledger = (
        InMemoryLedger()
        if backend == "fake"
        else SqliteLedger.open_control(
            tmp_path / "ledger.sqlite"  # type: ignore[operator]
        )
    )
    _, other_experiment_id, run_id, attempt_id = _seed_intent_ledger(ledger)
    result = ledger.submit_intent(  # type: ignore[union-attr]
        ArtifactLinkIntent(
            metadata(source_attempt_id=attempt_id, reason_code="artifact_recorded"),
            ArtifactLink(
                ArtifactRef.from_bytes(b"mismatched artifact"),
                "worker_export",
                experiment_id=other_experiment_id,
                run_id=run_id,
                attempt_id=attempt_id,
            ),
        )
    )
    assert isinstance(result, IntentRejection)
    assert result.reason_code == "artifact_experiment_run_mismatch"
    if isinstance(ledger, SqliteLedger):
        ledger.close()


def test_intent_delivery_is_idempotent_conflicts_fail_closed_and_rejections_are_thin(
    tmp_path: object,
) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    experiment_id, run_id, attempt_id = seed(ledger)
    first = RunTransitionIntent(
        metadata(
            source_attempt_id=attempt_id,
            expected_prior_state=RunState.PLANNED,
            expected_prior_digest=None,
            reason_code="lifecycle_advanced",
        ),
        run_id,
        RunState.PLANNED,
        RunState.ASSIGNED,
    )
    first_ack = accepted(ledger.submit_intent(first))
    repeated = accepted(ledger.submit_intent(first))
    assert repeated.idempotent is True
    assert repeated.canonical_payload_digest == first_ack.canonical_payload_digest

    conflicting = replace(
        first,
        metadata=replace(first.metadata, reason_code="different_reason"),
    )
    with pytest.raises(LedgerIntentConflictError):
        ledger.submit_intent(conflicting)

    stale = RunTransitionIntent(
        metadata(
            source_attempt_id=attempt_id,
            expected_prior_state=RunState.ASSIGNED,
            expected_prior_digest=None,
            reason_code="lifecycle_advanced",
        ),
        run_id,
        RunState.ASSIGNED,
        RunState.PROVISIONED,
    )
    stale_result = ledger.submit_intent(stale)
    assert isinstance(stale_result, IntentRejection)
    assert stale_result.reason_code == "stale_prior_digest"
    assert ledger.history(run_id)[-1].next_state is RunState.ASSIGNED

    thin_violation = CreateAttemptIntent(
        metadata(safe_metadata={"prompt": 1}),
        AttemptId.new(),
        run_id,
    )
    thin_result = ledger.submit_intent(thin_violation)
    assert isinstance(thin_result, IntentRejection)
    assert thin_result.reason_code == "thin_ledger_violation"
    duplicate_rejection = ledger.submit_intent(thin_violation)
    assert isinstance(duplicate_rejection, IntentRejection)
    assert duplicate_rejection.idempotent is True
    evidence = ledger.rejected_intent_evidence()
    assert {item.reason_code for item in evidence} == {
        "stale_prior_digest",
        "thin_ledger_violation",
    }
    assert all("prompt" not in repr(item).lower() for item in evidence)
    ledger.close()


def test_ledger_rejects_nonopaque_and_secret_bearing_input_without_identity_rows(
    tmp_path: object,
) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    unsafe_identity = CreateExperimentIntent(
        metadata(reason_code="control_recorded"),
        "private_repository_name",  # type: ignore[arg-type]
        ProtocolId.new(),
    )
    unsafe_metadata = CreateExperimentIntent(
        metadata(safe_metadata={"credential": 1}),
        ExperimentId.new(),
        ProtocolId.new(),
    )

    identity_result = ledger.submit_intent(unsafe_identity)
    metadata_result = ledger.submit_intent(unsafe_metadata)
    assert isinstance(identity_result, IntentRejection)
    assert identity_result.reason_code == "invalid_opaque_identity"
    assert isinstance(metadata_result, IntentRejection)
    assert metadata_result.reason_code == "thin_ledger_violation"
    assert ledger.logical_history() == ()
    assert len(ledger.rejected_intent_evidence()) == 2
    ledger.close()


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "prompt",
        "patch",
        "trace",
        "grader",
        "inspect",
        "provider_payload",
        "credential",
        "repository",
    ),
)
def test_thin_ledger_rejects_prohibited_evidence_metadata(
    tmp_path: object, forbidden_field: str
) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    result = ledger.submit_intent(
        CreateExperimentIntent(
            metadata(safe_metadata={forbidden_field: 1}),
            ExperimentId.new(),
            ProtocolId.new(),
        )
    )
    assert isinstance(result, IntentRejection)
    assert result.reason_code == "thin_ledger_violation"
    assert ledger.logical_history() == ()
    ledger.close()


@pytest.mark.parametrize("malformed_value", (float("inf"), float("nan")))
@pytest.mark.parametrize("backend", ("fake", "sqlite"))
def test_nonfinite_metadata_is_typed_rejection_before_canonicalization(
    tmp_path: object, backend: str, malformed_value: float
) -> None:
    ledger = (
        InMemoryLedger()
        if backend == "fake"
        else SqliteLedger.open_control(
            tmp_path / f"{backend}-{repr(malformed_value)}.sqlite"  # type: ignore[operator]
        )
    )
    intent = CreateExperimentIntent(
        metadata(safe_metadata={"duration": malformed_value}),
        ExperimentId.new(),
        ProtocolId.new(),
    )
    result = ledger.submit_intent(intent)  # type: ignore[union-attr]
    assert isinstance(result, IntentRejection)
    assert result.reason_code == "thin_ledger_violation"
    replay = ledger.submit_intent(intent)  # type: ignore[union-attr]
    assert isinstance(replay, IntentRejection)
    assert replay.idempotent is True
    if isinstance(ledger, SqliteLedger):
        evidence = ledger.rejected_intent_evidence()
        assert [item.reason_code for item in evidence] == ["thin_ledger_violation"]
        ledger.close()


@pytest.mark.parametrize("backend", ("fake", "sqlite"))
def test_invalid_evidence_ref_is_typed_rejection_before_canonicalization(
    tmp_path: object, backend: str
) -> None:
    ledger = (
        InMemoryLedger()
        if backend == "fake"
        else SqliteLedger.open_control(
            tmp_path / f"{backend}-invalid-evidence.sqlite"  # type: ignore[operator]
        )
    )
    intent = CreateExperimentIntent(
        metadata(evidence_refs=("unsafe evidence body",)),  # type: ignore[arg-type]
        ExperimentId.new(),
        ProtocolId.new(),
    )
    result = ledger.submit_intent(intent)  # type: ignore[union-attr]
    assert isinstance(result, IntentRejection)
    assert result.reason_code == "invalid_evidence_refs"
    replay = ledger.submit_intent(intent)  # type: ignore[union-attr]
    assert isinstance(replay, IntentRejection)
    assert replay.idempotent is True
    if isinstance(ledger, SqliteLedger):
        evidence = ledger.rejected_intent_evidence()
        assert [item.reason_code for item in evidence] == ["invalid_evidence_refs"]
        assert "unsafe evidence body" not in repr(evidence)
        ledger.close()


@pytest.mark.parametrize("backend", ("fake", "sqlite"))
def test_worker_control_routes_malformed_attempt_intents_to_typed_rejections(
    tmp_path: object, backend: str
) -> None:
    ledger = (
        InMemoryLedger()
        if backend == "fake"
        else SqliteLedger.open_control(
            tmp_path / f"{backend}-worker-malformed.sqlite"  # type: ignore[operator]
        )
    )
    control = LedgerControl(ledger)
    worker = WorkerIntentEmitter(control)
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    accepted(
        control.create_experiment(
            CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new())
        )
    )
    accepted(
        control.create_run(CreateRunIntent(metadata(), run_id, experiment_id, AssignmentId.new()))
    )
    accepted(control.create_initial_attempt(CreateAttemptIntent(metadata(), attempt_id, run_id)))

    control_rejected_nonfinite = worker.emit(
        CreateAttemptIntent(
            metadata(safe_metadata={"duration": float("inf")}),
            AttemptId.new(),
            run_id,
        )
    )
    assert isinstance(control_rejected_nonfinite, IntentRejection)
    assert control_rejected_nonfinite.reason_code == "thin_ledger_violation"
    control_rejected_evidence = worker.emit(
        CreateAttemptIntent(
            metadata(evidence_refs=("unsafe evidence body",)),  # type: ignore[arg-type]
            AttemptId.new(),
            run_id,
        )
    )
    assert isinstance(control_rejected_evidence, IntentRejection)
    assert control_rejected_evidence.reason_code == "invalid_evidence_refs"

    nonfinite = worker.emit(
        RunTransitionIntent(
            metadata(
                source_attempt_id=attempt_id,
                expected_prior_state=RunState.PLANNED,
                expected_prior_digest=None,
                safe_metadata={"duration": float("inf")},
                reason_code="lifecycle_advanced",
            ),
            run_id,
            RunState.PLANNED,
            RunState.ASSIGNED,
        )
    )
    assert isinstance(nonfinite, IntentRejection)
    assert nonfinite.reason_code == "thin_ledger_violation"
    invalid_evidence = worker.emit(
        ArtifactLinkIntent(
            metadata(
                source_attempt_id=attempt_id,
                evidence_refs=("unsafe evidence body",),  # type: ignore[arg-type]
                reason_code="artifact_recorded",
            ),
            ArtifactLink(
                ArtifactRef.from_bytes(b"valid linked artifact"),
                "worker_export",
                run_id=run_id,
                attempt_id=attempt_id,
            ),
        )
    )
    assert isinstance(invalid_evidence, IntentRejection)
    assert invalid_evidence.reason_code == "invalid_evidence_refs"
    if isinstance(ledger, SqliteLedger):
        evidence = ledger.rejected_intent_evidence()
        assert {item.reason_code for item in evidence} == {
            "thin_ledger_violation",
            "invalid_evidence_refs",
        }
        assert "unsafe evidence body" not in repr(evidence)
        ledger.close()


def test_terminal_attempts_and_authorized_retry_remain_separate_from_run_lifecycle(
    tmp_path: object,
) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    first_attempt = AttemptId.new()
    assignment_id = AssignmentId.new()
    accepted(
        ledger.submit_intent(CreateExperimentIntent(metadata(), experiment_id, ProtocolId.new()))
    )
    accepted(
        ledger.submit_intent(CreateRunIntent(metadata(), run_id, experiment_id, assignment_id))
    )
    accepted(ledger.submit_intent(CreateAttemptIntent(metadata(), first_attempt, run_id)))
    second_attempt = AttemptId.new()
    pre_exposure_evidence = ArtifactRef.from_bytes(b"pre-exposure infrastructure evidence")
    unverified_terminal = ledger.submit_intent(
        AttemptTerminalIntent(
            metadata(
                source_attempt_id=first_attempt,
                monotonic_ns=10,
                reason_code="infrastructure_pre_exposure",
            ),
            first_attempt,
            run_id,
            AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
        )
    )
    assert isinstance(unverified_terminal, IntentRejection)
    assert unverified_terminal.reason_code == "unverified_pre_exposure_failure"
    accepted(
        ledger.submit_intent(
            AttemptTerminalIntent(
                metadata(
                    source_attempt_id=first_attempt,
                    monotonic_ns=10,
                    reason_code="infrastructure_pre_exposure",
                    evidence_refs=(pre_exposure_evidence,),
                ),
                first_attempt,
                run_id,
                AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
                pre_exposure_evidence,
            )
        )
    )
    authorization = RetryAuthorization(
        run_id,
        assignment_id,
        assignment_id,
        first_attempt,
        Attempt(second_attempt, run_id),
        ledger.attempt_terminal_for(first_attempt),
        (ArtifactRef.from_bytes(b"unexposed"),),
        (ArtifactRef.from_bytes(b"fresh isolation"),),
    )
    assert ledger.append_retry_authorization_once(authorization) is True
    assert ledger.history(run_id) == ()
    assert ledger.attempt_terminals(run_id)[0].attempt_id == first_attempt
    assert ledger.retry_lineage(run_id) == ((first_attempt, second_attempt),)
    assert ledger.retry_authorizations_for(run_id) == (authorization,)
    unlinked_attempt = ledger.submit_intent(
        CreateAttemptIntent(metadata(), AttemptId.new(), run_id)
    )
    assert isinstance(unlinked_attempt, IntentRejection)
    assert unlinked_attempt.reason_code == "unlinked_attempt_creation"
    second_evidence = ArtifactRef.from_bytes(b"second pre-exposure evidence")
    accepted(
        ledger.submit_intent(
            AttemptTerminalIntent(
                metadata(
                    source_attempt_id=second_attempt,
                    reason_code="infrastructure_pre_exposure",
                    evidence_refs=(second_evidence,),
                ),
                second_attempt,
                run_id,
                AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
                second_evidence,
            )
        )
    )
    rejected = ledger.submit_intent(
        RetryLineageIntent(
            metadata(
                source_attempt_id=second_attempt,
                reason_code="authorized_retry",
            ),
            run_id,
            second_attempt,
            AttemptId.new(),
        )
    )
    assert isinstance(rejected, IntentRejection)
    assert rejected.reason_code == "retry_authorization_control_only"
    ledger.close()


def test_terminal_lifecycle_requires_matching_inclusion_decision(tmp_path: object) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    _, run_id, attempt_id = seed(ledger)
    digest: str | None = None
    state = RunState.PLANNED
    for monotonic_ns, next_state in enumerate(
        (
            RunState.ASSIGNED,
            RunState.PROVISIONED,
            RunState.RUNNING,
            RunState.EXPORTED,
            RunState.SCORED,
            RunState.RECONCILED,
        ),
        start=1,
    ):
        result = transition(
            ledger,
            run_id,
            attempt_id,
            state,
            next_state,
            digest,
            monotonic_ns=monotonic_ns,
        )
        digest = result.canonical_payload_digest
        state = next_state

    missing = ledger.submit_intent(
        RunTransitionIntent(
            metadata(
                source_attempt_id=attempt_id,
                expected_prior_state=RunState.RECONCILED,
                expected_prior_digest=digest,
                reason_code="lifecycle_advanced",
            ),
            run_id,
            RunState.RECONCILED,
            RunState.INCLUDED,
        )
    )
    assert isinstance(missing, IntentRejection)
    assert missing.reason_code == "missing_inclusion_decision"

    accepted(
        ledger.submit_intent(
            InclusionDecisionIntent(
                metadata(
                    source_attempt_id=attempt_id,
                    expected_prior_state=RunState.RECONCILED,
                    expected_prior_digest=digest,
                    reason_code="reconciliation_failed",
                ),
                InclusionDecision(
                    InclusionId.new(),
                    run_id,
                    InclusionStatus.EXCLUDED,
                    "reconciliation_failed",
                    "b" * 64,
                    NOW,
                ),
            )
        )
    )
    mismatch = ledger.submit_intent(
        RunTransitionIntent(
            metadata(
                source_attempt_id=attempt_id,
                expected_prior_state=RunState.RECONCILED,
                expected_prior_digest=digest,
                reason_code="lifecycle_advanced",
            ),
            run_id,
            RunState.RECONCILED,
            RunState.INCLUDED,
        )
    )
    assert isinstance(mismatch, IntentRejection)
    assert mismatch.reason_code == "inclusion_transition_mismatch"
    transition(
        ledger,
        run_id,
        attempt_id,
        RunState.RECONCILED,
        RunState.EXCLUDED,
        digest,
        monotonic_ns=7,
    )
    ledger.close()


def test_ledger_rejects_non_utc_times_and_never_adds_lifecycle_rows(tmp_path: object) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    _, run_id, attempt_id = seed(ledger)
    non_utc = IntentMetadata(
        IntentId.new(),
        (NOW + timedelta(hours=1)).replace(tzinfo=None),
        source_attempt_id=attempt_id,
        expected_prior_state=RunState.PLANNED,
        expected_prior_digest=None,
        reason_code="lifecycle_advanced",
    )
    result = ledger.submit_intent(
        RunTransitionIntent(non_utc, run_id, RunState.PLANNED, RunState.ASSIGNED)
    )
    assert isinstance(result, IntentRejection)
    assert ledger.history(run_id) == ()
    ledger.close()


def test_concurrent_control_deliveries_use_one_serial_writer_and_reject_stale_intent(
    tmp_path: object,
) -> None:
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")  # type: ignore[operator]
    _, run_id, attempt_id = seed(ledger)
    intents = tuple(
        RunTransitionIntent(
            metadata(
                source_attempt_id=attempt_id,
                expected_prior_state=RunState.PLANNED,
                expected_prior_digest=None,
                reason_code="lifecycle_advanced",
            ),
            run_id,
            RunState.PLANNED,
            RunState.ASSIGNED,
        )
        for _ in range(2)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(ledger.submit_intent, intents))

    assert sum(isinstance(result, IntentAck) for result in results) == 1
    assert [result.reason_code for result in results if isinstance(result, IntentRejection)] == [
        "stale_prior_state"
    ]
    assert [item.next_state for item in ledger.history(run_id)] == [RunState.ASSIGNED]
    assert ledger.integrity_check() == "ok"
    ledger.close()
