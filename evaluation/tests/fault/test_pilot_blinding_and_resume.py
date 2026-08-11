from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageAuthorizationId, StageId
from memrelay_eval.domain.states import StageKind
from memrelay_eval.orchestration.pilot import (
    FrozenPilotPlan,
    PilotBudget,
    PilotContracts,
    PilotPanelEvidence,
    PilotTask,
)
from memrelay_eval.orchestration.stages import StageAuthorization, StageUnit, plan_stage_resume
from memrelay_eval.scoring.blinding import LEAKAGE_AUC_UPPER_BOUND
from memrelay_eval.scoring.calibration import AGREEMENT_THRESHOLD, HUMAN_CALIBRATION_MAE_THRESHOLD
from memrelay_eval.scoring.reliability import CriterionAgreement, GateDecision, PanelGateEvidence
from memrelay_eval.scoring.rubric import JUDGE_CRITERIA

HASH = "a" * 64
NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def _authorization() -> StageAuthorization:
    return StageAuthorization(
        StageAuthorizationId.new(),
        StageId.new(),
        StageKind.PILOT,
        ProtocolId.new(),
        HASH,
        HASH,
        "operator",
        "operator",
        NOW - timedelta(minutes=1),
        NOW + timedelta(minutes=1),
        True,
    )


def _plan(*, duplicate_session: bool = False) -> FrozenPilotPlan:
    tasks = []
    for task in range(16):
        units = tuple(f"unit-{task}-{unit}" for unit in range(8))
        sessions = {
            unit: (f"session-{task}-{unit}-a", f"session-{task}-{unit}-b") for unit in units
        }
        tasks.append(PilotTask(f"task-{task}", units, sessions))
    if duplicate_session:
        duplicated = dict(tasks[-1].session_ids_by_unit)
        duplicated["unit-15-7"] = ("session-0-unit-0-0-a", "session-15-7-b")
        tasks[-1] = PilotTask("task-15", tasks[-1].assignment_unit_ids, duplicated)
    return FrozenPilotPlan(
        StageId.new(),
        ProtocolId.new(),
        tuple(tasks),
        PilotContracts(**dict.fromkeys(PilotContracts.__dataclass_fields__, HASH)),
        PilotBudget(1.0, 1.0, {"standard": 1}),
    )


def _panel(
    plan: FrozenPilotPlan,
    *,
    missing: bool = False,
    leakage_failed: bool = False,
) -> PilotPanelEvidence:
    agreement = GateDecision("passed", AGREEMENT_THRESHOLD, AGREEMENT_THRESHOLD, "minimum")
    gates = {
        "agreement": agreement,
        "human_calibration": GateDecision(
            "passed", HUMAN_CALIBRATION_MAE_THRESHOLD, HUMAN_CALIBRATION_MAE_THRESHOLD, "maximum"
        ),
    }
    if not missing:
        gates["leakage"] = GateDecision(
            "failed" if leakage_failed else "passed",
            LEAKAGE_AUC_UPPER_BOUND + 0.01 if leakage_failed else LEAKAGE_AUC_UPPER_BOUND,
            LEAKAGE_AUC_UPPER_BOUND,
            "maximum",
        )
    return PilotPanelEvidence(
        plan.stage_id,
        plan.protocol_id,
        plan.contracts.model_lock_sha256,
        plan.contracts.evidence_matrix_sha256,
        PanelGateEvidence(
            HASH,
            HASH,
            HASH,
            "diverse",
            {criterion: CriterionAgreement("icc", agreement) for criterion in JUDGE_CRITERIA},
            gates,
            {"judge": 0.0},
            dict.fromkeys(JUDGE_CRITERIA, 0.0),
            (),
        ),
    )


def test_started_and_terminal_units_never_resume_after_interruption() -> None:
    units = (
        StageUnit("terminal", terminal=True),
        StageUnit("started", terminal=False, started=True),
        StageUnit("never-started", terminal=False, started=False),
    )
    assert plan_stage_resume(
        units,
        authorization=_authorization(),
        now=NOW,
        locks_verified=True,
        receipts_consistent=True,
        ledger_cas_consistent=True,
        circuit_breaker_open=False,
    ) == ("never-started",)


def test_global_session_receipt_reuse_is_rejected() -> None:
    with pytest.raises(StageControlError) as failure:
        _plan(duplicate_session=True)
    assert failure.value.code == "pilot_session_receipt_reuse_forbidden"


@pytest.mark.parametrize(
    ("missing", "leakage_failed", "code"),
    (
        (True, False, "pilot_panel_required_gate_missing"),
        (False, True, "pilot_panel_or_blinding_gate_failed"),
    ),
)
def test_missing_panel_or_blinding_leakage_evidence_fails_closed(
    missing: bool, leakage_failed: bool, code: str
) -> None:
    plan = _plan()
    with pytest.raises(StageControlError) as failure:
        _panel(plan, missing=missing, leakage_failed=leakage_failed).bind(plan)
    assert failure.value.code == code


def test_pilot_panel_policy_uses_story_34_canonical_thresholds() -> None:
    panel = _panel(_plan()).panel

    assert panel.gates["agreement"].threshold == AGREEMENT_THRESHOLD
    assert panel.gates["human_calibration"].threshold == HUMAN_CALIBRATION_MAE_THRESHOLD
    assert panel.gates["leakage"].threshold == LEAKAGE_AUC_UPPER_BOUND
