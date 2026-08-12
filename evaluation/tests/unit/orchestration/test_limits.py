from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.orchestration.limits import (
    CircuitBreakerAdmissionController,
    CircuitBreakerDrainPolicy,
    CircuitBreakerReason,
    CircuitBreakerRecord,
    CircuitBreakerResumeAuthorization,
    CircuitBreakerState,
    CircuitBreakerTerminalEvidence,
    FrozenLimitEnvelope,
    load_circuit_breaker_record,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def _controller(
    *,
    stage_limits: dict[str, int | float] | None = None,
    run_limits: dict[str, int | float] | None = None,
) -> tuple[CircuitBreakerAdmissionController, RunId]:
    run_id = RunId.new()
    controller = CircuitBreakerAdmissionController(
        stage_id="stage-1",
        stage_envelope=FrozenLimitEnvelope(
            scope="stage",
            source_sha256=HASH_A,
            limits=stage_limits or {"copilot_tokens": 10},
        ),
        run_envelopes={
            run_id: FrozenLimitEnvelope(
                scope="run",
                source_sha256=HASH_B,
                limits=run_limits or {"copilot_tokens": 10},
            )
        },
        drain_policy=CircuitBreakerDrainPolicy(allow_active_cancellation=True),
    )
    return controller, run_id


def _terminal(attempt_id: AttemptId) -> CircuitBreakerTerminalEvidence:
    return CircuitBreakerTerminalEvidence(
        attempt_id=attempt_id,
        terminal_classification="cancelled_by_circuit_breaker",
        exposure_state="ambiguous",
        cost_quantities={"copilot_tokens": 1},
        evidence_refs=("native-ref", "ledger-ref", "telemetry-ref", "cost-ref"),
    )


def _resume_authorization(reason: CircuitBreakerReason) -> CircuitBreakerResumeAuthorization:
    return CircuitBreakerResumeAuthorization(
        authorization_id="authorization-1",
        authorizer_id="operator-1",
        authorizer_role="operator",
        stage_id="stage-1",
        reason=reason,
        stage_source_sha256=HASH_A,
        valid_from=datetime.now(UTC) - timedelta(minutes=1),
        valid_until=datetime.now(UTC) + timedelta(minutes=1),
    )


def test_exact_cap_admits_itt_attempt_then_stops_all_future_starts() -> None:
    controller, run_id = _controller(
        stage_limits={"copilot_tokens": 1},
        run_limits={"copilot_tokens": 1},
    )
    attempt_id = AttemptId.new()

    receipt = controller.admit(attempt_id, run_id, {"copilot_tokens": 1})

    assert receipt.attempt_id == attempt_id
    assert controller.state is CircuitBreakerState.DRAINING
    with pytest.raises(StageControlError) as failure:
        controller.admit(AttemptId.new(), run_id, {})
    assert failure.value.code == "circuit_breaker_new_attempts_stopped"

    controller.retain_terminal(_terminal(attempt_id))

    assert controller.state is CircuitBreakerState.CLOSED
    assert controller.terminal_evidence == (_terminal(attempt_id),)
    assert [record.state for record in controller.records] == [
        CircuitBreakerState.OPEN,
        CircuitBreakerState.TRIPPED,
        CircuitBreakerState.DRAINING,
        CircuitBreakerState.CLOSED,
    ]


def test_racing_starts_reserve_only_one_exact_cap_and_preserve_the_winner() -> None:
    controller, run_id = _controller(
        stage_limits={"tool_calls": 1},
        run_limits={"tool_calls": 1},
    )
    barrier = Barrier(8)

    def admit() -> str:
        barrier.wait()
        try:
            return str(controller.admit(AttemptId.new(), run_id, {"tool_calls": 1}).attempt_id)
        except StageControlError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _index: admit(), range(8)))

    successes = [
        outcome for outcome in outcomes if outcome != "circuit_breaker_new_attempts_stopped"
    ]
    assert len(successes) == 1
    assert outcomes.count("circuit_breaker_new_attempts_stopped") == 7
    assert len(controller.active_attempt_ids) == 1
    assert controller.state is CircuitBreakerState.DRAINING


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_nonfinite_limit_quantities_fail_closed(value: float) -> None:
    with pytest.raises(StageControlError) as failure:
        FrozenLimitEnvelope(
            scope="stage",
            source_sha256=HASH_A,
            limits={"framework_usd": value},
        )
    assert failure.value.code == "circuit_breaker_quantity_invalid"


def test_external_integrity_signal_is_idempotent_and_never_selects_a_fallback() -> None:
    controller, _run_id = _controller()

    controller.trip(CircuitBreakerReason.MODEL_UNAVAILABLE)
    controller.trip(CircuitBreakerReason.MODEL_UNAVAILABLE)

    assert controller.state is CircuitBreakerState.CLOSED
    assert [record.reason for record in controller.records] == [
        None,
        CircuitBreakerReason.MODEL_UNAVAILABLE,
        CircuitBreakerReason.MODEL_UNAVAILABLE,
    ]
    assert controller.status_projection()["reason"] == "model_unavailable"


def test_resume_requires_repair_evidence_and_independent_authorization() -> None:
    controller, _run_id = _controller()
    controller.trip(CircuitBreakerReason.THROTTLED)

    with pytest.raises(StageControlError) as failure:
        controller.resume(
            authorization=_resume_authorization(CircuitBreakerReason.QUOTA_EXHAUSTED),
            locks_unchanged=True,
            repair_evidence_verified=True,
            reconciliation_healthy=True,
            backup_healthy=True,
        )
    assert failure.value.code == "circuit_breaker_resume_authorization_invalid"

    controller.resume(
        authorization=_resume_authorization(CircuitBreakerReason.THROTTLED),
        locks_unchanged=True,
        repair_evidence_verified=True,
        reconciliation_healthy=True,
        backup_healthy=True,
    )

    assert controller.state is CircuitBreakerState.OPEN
    assert controller.records[1].reason is CircuitBreakerReason.THROTTLED


def test_observed_usage_cannot_decrease_a_reserved_cost_or_reopen_admission() -> None:
    controller, run_id = _controller(
        stage_limits={"framework_usd": 5},
        run_limits={"framework_usd": 5},
    )
    attempt_id = AttemptId.new()
    controller.admit(attempt_id, run_id, {"framework_usd": 4})

    controller.observe(run_id=run_id, quantities={"framework_usd": 1})

    status = controller.status_projection()
    assert status["stage"]["framework_usd"]["consumed"] == 4
    assert controller.state is CircuitBreakerState.OPEN


def test_stage_only_observation_accumulates_and_trips() -> None:
    controller, run_id = _controller(
        stage_limits={"tool_calls": 1},
        run_limits={"copilot_tokens": 10},
    )

    controller.observe(run_id=run_id, quantities={"tool_calls": 1})

    assert controller.state is CircuitBreakerState.CLOSED
    assert controller.status_projection()["stage"]["tool_calls"]["consumed"] == 1


def test_cancellation_requires_the_frozen_drain_policy() -> None:
    controller, run_id = _controller(stage_limits={"tool_calls": 1}, run_limits={"tool_calls": 1})
    attempt_id = AttemptId.new()
    controller.admit(attempt_id, run_id, {"tool_calls": 1})

    assert controller.may_cancel_active_attempt(attempt_id) is True


def test_non_boolean_drain_policy_is_refused() -> None:
    with pytest.raises(StageControlError) as failure:
        CircuitBreakerDrainPolicy(allow_active_cancellation="false")  # type: ignore[arg-type]
    assert failure.value.code == "circuit_breaker_drain_policy_invalid"


def test_journal_restart_stays_closed_until_repair_authorization() -> None:
    controller, run_id = _controller()
    controller.trip(CircuitBreakerReason.QUOTA_EXHAUSTED)

    recovered = CircuitBreakerAdmissionController(
        stage_id="stage-1",
        stage_envelope=FrozenLimitEnvelope(
            scope="stage",
            source_sha256=HASH_A,
            limits={"copilot_tokens": 10},
        ),
        run_envelopes={
            run_id: FrozenLimitEnvelope(
                scope="run",
                source_sha256=HASH_B,
                limits={"copilot_tokens": 10},
            )
        },
        journal=controller.records,
    )

    assert recovered.state is CircuitBreakerState.CLOSED
    with pytest.raises(StageControlError) as failure:
        recovered.admit(AttemptId.new(), run_id, {"copilot_tokens": 1})
    assert failure.value.code == "circuit_breaker_new_attempts_stopped"


def test_resume_authorization_is_consumed_after_one_trip_instance() -> None:
    controller, _run_id = _controller()
    authorization = _resume_authorization(CircuitBreakerReason.THROTTLED)
    controller.trip(CircuitBreakerReason.THROTTLED)
    controller.resume(
        authorization=authorization,
        locks_unchanged=True,
        repair_evidence_verified=True,
        reconciliation_healthy=True,
        backup_healthy=True,
    )
    controller.trip(CircuitBreakerReason.THROTTLED)

    with pytest.raises(StageControlError) as failure:
        controller.resume(
            authorization=authorization,
            locks_unchanged=True,
            repair_evidence_verified=True,
            reconciliation_healthy=True,
            backup_healthy=True,
        )
    assert failure.value.code == "circuit_breaker_resume_authorization_invalid"


def test_recovery_preserves_cumulative_stage_and_run_headroom_after_resume() -> None:
    controller, run_id = _controller(
        stage_limits={"copilot_tokens": 10},
        run_limits={"copilot_tokens": 10},
    )
    attempt_id = AttemptId.new()
    controller.admit(attempt_id, run_id, {"copilot_tokens": 5})
    controller.trip(CircuitBreakerReason.THROTTLED)
    controller.retain_terminal(_terminal(attempt_id))

    recovered = CircuitBreakerAdmissionController(
        stage_id="stage-1",
        stage_envelope=FrozenLimitEnvelope(
            scope="stage",
            source_sha256=HASH_A,
            limits={"copilot_tokens": 10},
        ),
        run_envelopes={
            run_id: FrozenLimitEnvelope(
                scope="run",
                source_sha256=HASH_B,
                limits={"copilot_tokens": 10},
            )
        },
        drain_policy=CircuitBreakerDrainPolicy(allow_active_cancellation=True),
        journal=controller.records,
    )
    recovered.resume(
        authorization=_resume_authorization(CircuitBreakerReason.THROTTLED),
        locks_unchanged=True,
        repair_evidence_verified=True,
        reconciliation_healthy=True,
        backup_healthy=True,
    )

    with pytest.raises(StageControlError) as failure:
        recovered.admit(AttemptId.new(), run_id, {"copilot_tokens": 6})
    assert failure.value.code == "circuit_breaker_limit_exceeded"
    status = recovered.status_projection()
    assert status["stage"]["copilot_tokens"]["consumed"] == 5
    assert status["runs"][str(run_id)]["copilot_tokens"]["consumed"] == 5


def test_recovery_never_resets_an_exact_stage_or_run_cap() -> None:
    controller, run_id = _controller(
        stage_limits={"copilot_tokens": 5},
        run_limits={"copilot_tokens": 5},
    )
    attempt_id = AttemptId.new()
    controller.admit(attempt_id, run_id, {"copilot_tokens": 5})
    controller.retain_terminal(_terminal(attempt_id))

    recovered = CircuitBreakerAdmissionController(
        stage_id="stage-1",
        stage_envelope=FrozenLimitEnvelope(
            scope="stage",
            source_sha256=HASH_A,
            limits={"copilot_tokens": 5},
        ),
        run_envelopes={
            run_id: FrozenLimitEnvelope(
                scope="run",
                source_sha256=HASH_B,
                limits={"copilot_tokens": 5},
            )
        },
        journal=controller.records,
    )

    with pytest.raises(StageControlError) as resume_failure:
        recovered.resume(
            authorization=_resume_authorization(CircuitBreakerReason.LIMIT_EXCEEDED),
            locks_unchanged=True,
            repair_evidence_verified=True,
            reconciliation_healthy=True,
            backup_healthy=True,
        )
    assert resume_failure.value.code == "circuit_breaker_resume_limits_exhausted"
    with pytest.raises(StageControlError) as admission_failure:
        recovered.admit(AttemptId.new(), run_id, {"copilot_tokens": 1})
    assert admission_failure.value.code == "circuit_breaker_new_attempts_stopped"


def test_recovery_retains_partial_unclaimed_reservation_and_terminal_evidence() -> None:
    controller, run_id = _controller(
        stage_limits={"copilot_tokens": 10},
        run_limits={"copilot_tokens": 10},
    )
    active_attempt = AttemptId.new()
    terminal_attempt = AttemptId.new()
    controller.admit(active_attempt, run_id, {"copilot_tokens": 2})
    controller.admit(terminal_attempt, run_id, {"copilot_tokens": 3})
    controller.trip(CircuitBreakerReason.QUOTA_EXHAUSTED)
    controller.retain_terminal(_terminal(terminal_attempt))

    recovered = CircuitBreakerAdmissionController(
        stage_id="stage-1",
        stage_envelope=FrozenLimitEnvelope(
            scope="stage",
            source_sha256=HASH_A,
            limits={"copilot_tokens": 10},
        ),
        run_envelopes={
            run_id: FrozenLimitEnvelope(
                scope="run",
                source_sha256=HASH_B,
                limits={"copilot_tokens": 10},
            )
        },
        drain_policy=CircuitBreakerDrainPolicy(allow_active_cancellation=True),
        journal=controller.records,
    )

    assert recovered.active_attempt_ids == (active_attempt,)
    assert recovered.terminal_evidence == (_terminal(terminal_attempt),)
    assert recovered.state is CircuitBreakerState.DRAINING


@pytest.mark.parametrize(
    "journal",
    (
        lambda records: (records[-1],),
        lambda records: (records[0], records[0]),
        lambda records: (
            *records[:-1],
            replace(records[-1], snapshot={"stage_consumed": {}}),
        ),
    ),
)
def test_recovery_rejects_missing_duplicate_or_tampered_journal(
    journal: Callable[[tuple[CircuitBreakerRecord, ...]], tuple[CircuitBreakerRecord, ...]],
) -> None:
    controller, run_id = _controller()
    controller.admit(AttemptId.new(), run_id, {"copilot_tokens": 1})
    records = controller.records

    with pytest.raises(StageControlError) as failure:
        CircuitBreakerAdmissionController(
            stage_id="stage-1",
            stage_envelope=FrozenLimitEnvelope(
                scope="stage",
                source_sha256=HASH_A,
                limits={"copilot_tokens": 10},
            ),
            run_envelopes={
                run_id: FrozenLimitEnvelope(
                    scope="run",
                    source_sha256=HASH_B,
                    limits={"copilot_tokens": 10},
                )
            },
            journal=journal(records),
        )
    assert failure.value.code == "circuit_breaker_journal_invalid"


def test_canonical_record_loader_rejects_byte_or_digest_tampering() -> None:
    controller, _run_id = _controller()
    record = controller.records[0]

    assert load_circuit_breaker_record(record.bytes()) == record

    tampered = record.bytes().replace(b'"state":"open"', b'"state":"closed"')
    with pytest.raises(StageControlError) as failure:
        load_circuit_breaker_record(tampered)
    assert failure.value.code == "circuit_breaker_journal_invalid"


@pytest.mark.parametrize("value", (float("nan"), float("inf"), 10**1000))
def test_recovery_rejects_nonfinite_or_overflow_snapshot_quantity(value: float | int) -> None:
    controller, run_id = _controller()
    controller.admit(AttemptId.new(), run_id, {"copilot_tokens": 1})
    records = controller.records
    final = records[-1]
    snapshot = dict(final.snapshot)
    snapshot["stage_consumed"] = {"copilot_tokens": value}

    with pytest.raises(StageControlError) as failure:
        CircuitBreakerAdmissionController(
            stage_id="stage-1",
            stage_envelope=FrozenLimitEnvelope(
                scope="stage",
                source_sha256=HASH_A,
                limits={"copilot_tokens": 10},
            ),
            run_envelopes={
                run_id: FrozenLimitEnvelope(
                    scope="run",
                    source_sha256=HASH_B,
                    limits={"copilot_tokens": 10},
                )
            },
            journal=(*records[:-1], replace(final, snapshot=snapshot)),
        )
    assert failure.value.code == "circuit_breaker_journal_invalid"
