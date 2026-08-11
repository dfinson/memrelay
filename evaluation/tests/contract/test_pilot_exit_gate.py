from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from memrelay_eval.analysis.gates import CategoricalGateDecision
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageId
from memrelay_eval.orchestration.pilot import (
    FrozenPilotPlan,
    FrozenPowerPublication,
    PilotBudget,
    PilotCategoricalGateEvidence,
    PilotContracts,
    PilotEvidenceCompleteness,
    PilotExitEvidence,
    PilotExitStore,
    PilotPanelEvidence,
    PilotTask,
    evaluate_pilot_exit,
    require_fresh_pilot_stage,
)
from memrelay_eval.scoring.blinding import LEAKAGE_AUC_UPPER_BOUND
from memrelay_eval.scoring.calibration import AGREEMENT_THRESHOLD, HUMAN_CALIBRATION_MAE_THRESHOLD
from memrelay_eval.scoring.reliability import CriterionAgreement, GateDecision, PanelGateEvidence
from memrelay_eval.scoring.rubric import JUDGE_CRITERIA

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


def _panel(plan: FrozenPilotPlan) -> PilotPanelEvidence:
    gates = {
        "agreement": GateDecision("passed", AGREEMENT_THRESHOLD, AGREEMENT_THRESHOLD, "minimum"),
        "human_calibration": GateDecision(
            "passed", HUMAN_CALIBRATION_MAE_THRESHOLD, HUMAN_CALIBRATION_MAE_THRESHOLD, "maximum"
        ),
        "leakage": GateDecision(
            "passed", LEAKAGE_AUC_UPPER_BOUND, LEAKAGE_AUC_UPPER_BOUND, "maximum"
        ),
    }
    panel = PanelGateEvidence(
        HASH,
        HASH,
        HASH,
        "diverse",
        {criterion: CriterionAgreement("icc", gates["agreement"]) for criterion in JUDGE_CRITERIA},
        gates,
        {"judge": 0.0},
        dict.fromkeys(JUDGE_CRITERIA, 0.0),
        (),
    )
    return PilotPanelEvidence(
        plan.stage_id,
        plan.protocol_id,
        plan.contracts.model_lock_sha256,
        plan.contracts.evidence_matrix_sha256,
        panel,
    )


def _categorical_gates(
    plan: FrozenPilotPlan, *, failed: str | None = None
) -> tuple[PilotCategoricalGateEvidence, ...]:
    return tuple(
        PilotCategoricalGateEvidence(
            gate,
            plan.stage_id,
            plan.protocol_id,
            plan.contracts.model_lock_sha256,
            plan.contracts.evidence_matrix_sha256,
            CategoricalGateDecision(
                f"{plan.stage_id}:{gate}",
                "blocked" if gate == failed else "pass",
                ("event",) if gate == failed else (),
                ("claim",) if gate == failed else (),
                HASH,
                (HASH,) if gate == failed else (),
                gate == failed,
            ),
        )
        for gate in ("security", "governance", "grading", "evidence", "causal")
    )


def _evidence(plan: FrozenPilotPlan, *, missing_mandatory: bool = False) -> PilotExitEvidence:
    items = {f"item-{index}": True for index in range(100)}
    if missing_mandatory:
        items["item-0"] = False
    return PilotExitEvidence(
        plan_sha256=plan.digest,
        completeness=PilotEvidenceCompleteness(items, ("item-0",)),
        panel=_panel(plan),
        categorical_gates=_categorical_gates(plan),
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
            evidence.categorical_gates,
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
        require_fresh_pilot_stage(plan, decision, plan)
    assert failure.value.code == "pilot_fresh_stage_id_required"


def test_failed_security_gate_rejects_even_with_complete_evidence() -> None:
    plan = _plan()
    evidence = _evidence(plan)
    decision = evaluate_pilot_exit(
        plan,
        PilotExitEvidence(
            plan.digest,
            evidence.completeness,
            evidence.panel,
            _categorical_gates(plan, failed="security"),
            HASH,
            HASH,
            HASH,
            HASH,
            evidence.power,
        ),
    )

    assert decision.status == "rejected"
    assert "pilot_security_gate_blocked" in decision.failure_codes


def test_exact_98_percent_evidence_completeness_is_accepted() -> None:
    plan = _plan()
    base = _evidence(plan)
    items = {f"item-{index}": index < 9_800 for index in range(10_000)}
    decision = evaluate_pilot_exit(
        plan,
        PilotExitEvidence(
            plan.digest,
            PilotEvidenceCompleteness(items, ("item-0",)),
            base.panel,
            base.categorical_gates,
            HASH,
            HASH,
            HASH,
            HASH,
            base.power,
        ),
    )
    assert decision.status == "accepted"


def test_terminal_decision_reuses_identical_bytes_and_rejects_regrade(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plan = _plan()
    store = PilotExitStore(tmp_path)
    evidence = _evidence(plan)
    first, path, outcome = store.gate(plan, evidence)
    second, repeated_path, repeat_outcome = store.gate(plan, evidence)

    assert outcome == "sealed"
    assert repeat_outcome == "reused"
    assert path == repeated_path
    assert first == second

    with pytest.raises(StageControlError) as failure:
        store.gate(
            plan,
            PilotExitEvidence(
                plan.digest,
                evidence.completeness,
                evidence.panel,
                _categorical_gates(plan, failed="security"),
                HASH,
                HASH,
                HASH,
                HASH,
                evidence.power,
            ),
        )
    assert failure.value.code == "pilot_stage_mutation_regrade_prohibited"


def test_rejected_stage_cannot_be_regraded_to_accepted_or_salvaged(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plan = _plan()
    store = PilotExitStore(tmp_path)
    rejected = PilotExitEvidence(
        plan.digest,
        _evidence(plan).completeness,
        _panel(plan),
        _categorical_gates(plan, failed="security"),
        HASH,
        HASH,
        HASH,
        HASH,
        FrozenPowerPublication(HASH, HASH, HASH, ("registered-cell",), {"registered-cell": 10_000}),
    )
    decision, _path, _outcome = store.gate(plan, rejected)
    assert decision.status == "rejected"
    with pytest.raises(StageControlError) as failure:
        store.gate(plan, _evidence(plan))
    assert failure.value.code == "pilot_stage_mutation_regrade_prohibited"


def test_rejected_pilot_requires_a_new_stage_protocol_and_plan() -> None:
    plan = _plan()
    decision = evaluate_pilot_exit(
        plan,
        PilotExitEvidence(
            plan.digest,
            _evidence(plan).completeness,
            _panel(plan),
            _categorical_gates(plan, failed="security"),
            HASH,
            HASH,
            HASH,
            HASH,
            FrozenPowerPublication(
                HASH, HASH, HASH, ("registered-cell",), {"registered-cell": 10_000}
            ),
        ),
    )
    with pytest.raises(StageControlError) as failure:
        require_fresh_pilot_stage(plan, decision, replace(plan, stage_id=StageId.new()))
    assert failure.value.code == "pilot_fresh_protocol_id_required"
    require_fresh_pilot_stage(plan, decision, _plan())


def test_concurrent_terminal_writers_allow_only_identical_replay(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plan = _plan()
    evidence = _evidence(plan)
    store = PilotExitStore(tmp_path)
    barrier = Barrier(2)

    def gate() -> str:
        barrier.wait()
        return store.gate(plan, evidence)[2]

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: gate(), range(2)))
    assert sorted(outcomes) == ["reused", "sealed"]
