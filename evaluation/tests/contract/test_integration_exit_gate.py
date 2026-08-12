from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageId
from memrelay_eval.orchestration.integration import (
    INTEGRATION_REQUIRED_SUMMARIES,
    IntegrationAttemptEvidence,
    IntegrationExitDecision,
    IntegrationExitEvidence,
    IntegrationExitStore,
    evaluate_integration_exit,
    require_fresh_integration_stage,
)

from tests.unit.orchestration.test_integration_plan import _plan

HASH = "a" * 64


def _evidence(
    plan,
    *,
    complete_count: int = 32,
    reconciled: bool = True,
    blocker: str | None = None,
) -> IntegrationExitEvidence:
    attempts = tuple(
        IntegrationAttemptEvidence(
            run_id=run.run_id,
            attempt_number=0,
            terminal_kind="succeeded",
            exposure="exposed",
            infrastructure_complete=index < complete_count,
            reconciled=reconciled,
            evidence_refs=(f"evidence-{index}",),
        )
        for index, run in enumerate(plan.runs)
    )
    statuses = dict.fromkeys(("security", "governance", "grading", "evidence", "causal"), "pass")
    if blocker is not None:
        statuses[blocker] = "blocked"
    return IntegrationExitEvidence(
        plan_sha256=plan.digest,
        attempts=attempts,
        summary_sha256=dict.fromkeys(INTEGRATION_REQUIRED_SUMMARIES, HASH),
        categorical_gate_statuses=statuses,
    )


@pytest.mark.parametrize(
    ("complete_count", "status"),
    ((29, "rejected"), (30, "accepted"), (32, "accepted")),
)
def test_integration_exit_enforces_exact_29_30_32_boundaries(
    complete_count: int, status: str
) -> None:
    plan, _entry, _limits = _plan()

    decision = evaluate_integration_exit(plan, _evidence(plan, complete_count=complete_count))

    assert decision.status == status
    assert decision.infrastructure_complete_count == complete_count
    assert decision.to_document()["assigned_run_denominator"] == 32


def test_integration_exit_rejects_incomplete_terminal_evidence() -> None:
    plan, _entry, _limits = _plan()

    decision = evaluate_integration_exit(plan, _evidence(plan, reconciled=False))

    assert decision.status == "rejected"
    assert "integration_terminal_evidence_incomplete" in decision.failure_codes


@pytest.mark.parametrize("blocker", ("security", "governance", "grading", "evidence", "causal"))
def test_each_categorical_blocker_rejects_the_entire_stage(blocker: str) -> None:
    plan, _entry, _limits = _plan()

    decision = evaluate_integration_exit(plan, _evidence(plan, blocker=blocker))

    assert decision.status == "rejected"
    assert "integration_categorical_blockers_present" in decision.failure_codes


def test_exit_is_immutable_and_rejected_stage_requires_a_fresh_full_plan(tmp_path) -> None:
    plan, _entry, _limits = _plan()
    store = IntegrationExitStore(tmp_path)
    rejected = _evidence(plan, complete_count=29)

    decision, path, outcome = store.gate(plan, rejected)
    assert decision.status == "rejected"
    assert outcome == "sealed"
    assert store.gate(plan, rejected)[1:] == (path, "reused")
    with pytest.raises(StageControlError) as failure:
        store.gate(plan, _evidence(plan, complete_count=32))
    assert failure.value.code == "integration_stage_mutation_regrade_prohibited"

    with pytest.raises(StageControlError) as failure:
        require_fresh_integration_stage(plan, decision, replace(plan, stage_id=StageId.new()))
    assert failure.value.code == "integration_fresh_protocol_id_required"
    replacement = replace(plan, stage_id=StageId.new(), protocol_id=ProtocolId.new())
    require_fresh_integration_stage(plan, decision, replacement)


def test_integration_exit_decision_matches_the_stable_golden_bundle() -> None:
    golden_path = Path(__file__).parents[1] / "golden" / "integration_exit_bundle.json"
    expected = json.loads(golden_path.read_text(encoding="utf-8"))

    decision = IntegrationExitDecision(
        plan_sha256="a" * 64,
        exit_evidence_sha256="b" * 64,
        status="rejected",
        infrastructure_complete_count=29,
        failure_codes=("integration_infrastructure_complete_below_30",),
    )

    assert decision.to_document() == expected
