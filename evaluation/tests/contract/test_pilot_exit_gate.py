from __future__ import annotations

import pytest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageId
from memrelay_eval.orchestration.pilot import (
    FrozenPilotPlan,
    FrozenPowerPublication,
    PilotBudget,
    PilotContracts,
    PilotEvidenceCompleteness,
    PilotExitEvidence,
    PilotPanelMetrics,
    PilotTask,
    evaluate_pilot_exit,
    require_fresh_pilot_stage,
)

HASH = "a" * 64


def _plan() -> FrozenPilotPlan:
    return FrozenPilotPlan(
        stage_id=StageId.new(),
        protocol_id=ProtocolId.new(),
        tasks=tuple(
            PilotTask(
                f"task-{task}",
                tuple(f"unit-{task}-{unit}" for unit in range(8)),
                {
                    f"unit-{task}-{unit}": (f"session-{task}-{unit}-a", f"session-{task}-{unit}-b")
                    for unit in range(8)
                },
            )
            for task in range(16)
        ),
        contracts=PilotContracts(**dict.fromkeys(PilotContracts.__dataclass_fields__, HASH)),
        budget=PilotBudget(1.0, 1.0, {"standard": 1}),
    )


def _evidence(plan: FrozenPilotPlan, *, missing_mandatory: bool = False) -> PilotExitEvidence:
    items = {f"item-{index}": True for index in range(100)}
    if missing_mandatory:
        items["item-0"] = False
    return PilotExitEvidence(
        plan_sha256=plan.digest,
        completeness=PilotEvidenceCompleteness(items, ("item-0",)),
        panel=PilotPanelMetrics(0.70, 0.10, 0.60),
        required_gate_statuses=dict.fromkeys(
            ("panel", "blinding", "security", "governance", "grading", "evidence", "causal"),
            "passed",
        ),
        variance_sha256=HASH,
        icc_sha256=HASH,
        attrition_sha256=HASH,
        harm_sha256=HASH,
        power=FrozenPowerPublication(
            HASH, HASH, HASH, ("registered-cell",), {"registered-cell": 10_000}
        ),
    )


def test_exact_completeness_and_panel_boundaries_pass_without_confirmatory_promotion() -> None:
    plan = _plan()
    decision = evaluate_pilot_exit(plan, _evidence(plan))

    assert decision.status == "accepted"
    assert decision.confirmation_eligible is False
    assert decision.to_document()["evidence_classification"] == "non-confirmatory"
    assert decision.to_document()["exit_evidence_sha256"] == _evidence(plan).digest


@pytest.mark.parametrize("missing_mandatory", (True, False))
def test_missing_mandatory_or_9799_percent_evidence_rejects_whole_pilot(
    missing_mandatory: bool,
) -> None:
    plan = _plan()
    evidence = _evidence(plan, missing_mandatory=missing_mandatory)
    if not missing_mandatory:
        items = {f"item-{index}": True for index in range(10_000)}
        for index in range(201, 10_000):
            items[f"item-{index}"] = False
        evidence = PilotExitEvidence(
            plan.digest,
            PilotEvidenceCompleteness(items, ("item-0",)),
            evidence.panel,
            evidence.required_gate_statuses,
            HASH,
            HASH,
            HASH,
            HASH,
            evidence.power,
        )
    decision = evaluate_pilot_exit(plan, evidence)

    assert decision.status == "rejected"
    assert decision.fresh_stage_required
    with pytest.raises(StageControlError) as failure:
        require_fresh_pilot_stage(plan, decision, plan.stage_id)
    assert failure.value.code == "pilot_fresh_stage_id_required"


def test_failed_security_gate_rejects_even_with_complete_evidence() -> None:
    plan = _plan()
    evidence = _evidence(plan)
    statuses = dict(evidence.required_gate_statuses)
    statuses["security"] = "failed"
    decision = evaluate_pilot_exit(
        plan,
        PilotExitEvidence(
            plan.digest,
            evidence.completeness,
            evidence.panel,
            statuses,
            HASH,
            HASH,
            HASH,
            HASH,
            evidence.power,
        ),
    )

    assert decision.status == "rejected"
    assert "pilot_security_gate_failed" in decision.failure_codes
