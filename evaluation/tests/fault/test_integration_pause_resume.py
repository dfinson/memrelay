from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import AttemptId
from memrelay_eval.orchestration.integration import (
    INTEGRATION_REQUIRED_SUMMARIES,
    IntegrationAttemptEvidence,
    IntegrationReceiptJournal,
)
from memrelay_eval.orchestration.limits import (
    CircuitBreakerAdmissionController,
    CircuitBreakerState,
)
from tests.unit.orchestration.test_integration_plan import HASH, _plan


def _terminal(run_id, attempt_number: int, *, retry_source: bool = False):
    return IntegrationAttemptEvidence(
        run_id=run_id,
        attempt_number=attempt_number,
        terminal_kind=("infrastructure_failed_pre_exposure" if retry_source else "succeeded"),
        exposure="unexposed" if retry_source else "exposed",
        infrastructure_complete=not retry_source,
        reconciled=True,
        evidence_refs=(f"receipt-{run_id}-{attempt_number}",),
    )


def test_pause_resume_starts_only_never_started_runs_and_preserves_retry_evidence(
    tmp_path,
) -> None:
    plan, _entry, _limits = _plan()
    journal = IntegrationReceiptJournal(plan, tmp_path)
    first, retryable, untouched = (run.run_id for run in plan.runs[:3])

    first_attempt = journal.start(first)
    journal.terminal(first_attempt, _terminal(first, 0))
    retry_source_attempt = journal.start(retryable)
    journal.terminal(retry_source_attempt, _terminal(retryable, 0, retry_source=True))

    resumed = journal.resume_candidates()
    assert first not in resumed
    assert retryable not in resumed
    assert untouched in resumed
    with pytest.raises(StageControlError, match="already started"):
        journal.start(first)

    retry_attempt = journal.start(retryable, retry=True)
    journal.terminal(retry_attempt, _terminal(retryable, 1))
    with pytest.raises(StageControlError, match="retry not authorized|already used"):
        journal.start(retryable, retry=True)


def test_duplicate_concurrent_start_has_one_idempotent_receipt(tmp_path) -> None:
    plan, _entry, _limits = _plan()
    journal = IntegrationReceiptJournal(plan, tmp_path)
    run_id = plan.runs[0].run_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        attempt_ids = tuple(executor.map(lambda _: journal.start(run_id), range(8)))

    assert len(set(attempt_ids)) == 1
    journal.terminal(attempt_ids[0], _terminal(run_id, 0))
    with pytest.raises(StageControlError, match="already started"):
        journal.start(run_id)


def test_cap_trip_stops_new_attempts_without_erasing_active_evidence() -> None:
    plan, _entry, limits = _plan()
    controller = CircuitBreakerAdmissionController(
        stage_id=str(plan.stage_id),
        stage_envelope=limits.stage_envelope(HASH),
        run_envelopes={run.run_id: limits.run_envelope(HASH) for run in plan.runs},
    )
    run_id = plan.runs[0].run_id
    active_attempt = AttemptId.new()
    controller.admit(
        active_attempt,
        run_id,
        {"tool_calls": limits.per_run_tool_call_cap},
    )
    controller.observe(
        run_id=run_id,
        quantities={"tool_calls": limits.per_run_tool_call_cap},
    )

    assert controller.state in {CircuitBreakerState.DRAINING, CircuitBreakerState.TRIPPED}
    with pytest.raises(StageControlError, match="new attempts stopped"):
        controller.admit(AttemptId.new(), plan.runs[1].run_id, {"tool_calls": 1})
    assert active_attempt in controller.active_attempt_ids


def test_interruption_cannot_publish_exit_while_active_attempts_remain(tmp_path) -> None:
    plan, _entry, _limits = _plan()
    journal = IntegrationReceiptJournal(plan, tmp_path)
    journal.start(plan.runs[0].run_id)

    with pytest.raises(StageControlError, match="attempts not terminal"):
        journal.evidence(
            dict.fromkeys(INTEGRATION_REQUIRED_SUMMARIES, HASH),
            dict.fromkeys(("security", "governance", "grading", "evidence", "causal"), "pass"),
        )


def test_restart_recovers_receipts_and_enforces_bounded_lanes(tmp_path) -> None:
    plan, _entry, _limits = _plan()
    journal = IntegrationReceiptJournal(plan, tmp_path)
    first = journal.start(plan.runs[0].run_id)
    second = journal.start(plan.runs[1].run_id)

    recovered = IntegrationReceiptJournal(plan, tmp_path)
    assert plan.runs[0].run_id not in recovered.resume_candidates()
    assert plan.runs[1].run_id not in recovered.resume_candidates()
    with pytest.raises(StageControlError, match="concurrency cap reached"):
        recovered.start(plan.runs[2].run_id)

    journal.terminal(first, _terminal(plan.runs[0].run_id, 0))
    recovered = IntegrationReceiptJournal(plan, tmp_path)
    third = recovered.start(plan.runs[2].run_id)
    assert third
    with pytest.raises(StageControlError, match="schedule order violation"):
        recovered.start(plan.runs[4].run_id)
    assert second
