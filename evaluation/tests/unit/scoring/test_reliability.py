from __future__ import annotations

from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.scoring.blinding import LeakageConformance
from memrelay_eval.scoring.calibration import (
    FrozenPanelPassRules,
    FrozenPanelQualificationProtocol,
    HumanGoldLabel,
)
from memrelay_eval.scoring.reliability import (
    evaluate_panel_reliability,
    write_panel_gate_evidence,
)
from memrelay_eval.scoring.rubric import (
    JUDGE_CRITERIA,
    FrozenPanelSchedule,
    JudgeCriterionScore,
    JudgeLimits,
    JudgeRecord,
)


def _schedule() -> FrozenPanelSchedule:
    return FrozenPanelSchedule(
        ("candidate-a",),
        ("calibration-a",),
        ("duplicate-a",),
        ("sentinel-a",),
        "a" * 64,
        JudgeLimits(10, 1, 1, 1, 12, 120, 12, 12, 12),
    )


def _protocol(
    schedule: FrozenPanelSchedule, *, stronger_threshold: float | None = None
) -> FrozenPanelQualificationProtocol:
    labels = dict.fromkeys(JUDGE_CRITERIA, 0.75)
    return FrozenPanelQualificationProtocol(
        schedule_sha256=schedule.sha256,
        gold_label_provenance_sha256="b" * 64,
        human_gold_labels=(
            HumanGoldLabel("calibration-a", labels),
            HumanGoldLabel("sentinel-a", labels),
        ),
        duplicate_pairs=(("duplicate-a", "candidate-a"),),
        criterion_metrics=tuple((criterion, "icc") for criterion in JUDGE_CRITERIA),
        drift_window_size=1,
        pass_rules=FrozenPanelPassRules(0.01, 0.01, 0.01, 0.01, 0.01, 0.01),
        generator_model_family="family-one",
        stronger_human_calibration_mae_threshold=stronger_threshold,
        sensitivity_rule_version="family-mean-difference-v1",
    )


def _record(
    candidate_id: str,
    position: int,
    slot: int,
    family: str,
    *,
    score: float = 0.75,
    diversity: str = "diverse",
    status: str = "completed",
) -> JudgeRecord:
    citation = f"artifact://blinded/{'a' * 64}"
    criteria = (
        {criterion: JudgeCriterionScore(score, 0.1, (citation,)) for criterion in JUDGE_CRITERIA}
        if status == "completed"
        else {}
    )
    return JudgeRecord(
        candidate_id=candidate_id,
        view_sha256="c" * 64,
        panel_protocol_sha256="d" * 64,
        schedule_position=position,
        judge_slot=slot,
        model_id=f"judge-{slot}",
        model_family=family,
        diversity_label=diversity,
        requires_stronger_calibration=diversity != "diverse",
        runtime_lock_sha256="e" * 64,
        model_lock_sha256="f" * 64,
        rubric_sha256="0" * 64,
        system_prompt_sha256="1" * 64,
        tools_sha256="2" * 64,
        decoding_controls_sha256="3" * 64,
        status=status,
        criteria=criteria,
        failure_code="judge_timeout" if status != "completed" else None,
    )


def _records(*, diversity: str = "diverse", score: float = 0.75) -> tuple[JudgeRecord, ...]:
    families = ("family-one", "family-two", "family-three")
    return tuple(
        _record(item_id, position, slot, families[slot - 1], diversity=diversity, score=score)
        for position, item_id in enumerate(_schedule().item_ids)
        for slot in (1, 2, 3)
    )


def _leakage() -> LeakageConformance:
    return LeakageConformance("4" * 64, 0.5, 0.60, ())


def test_reliability_uses_all_frozen_gates_and_retains_fake_authority_blocker() -> None:
    schedule = _schedule()
    evidence = evaluate_panel_reliability(_protocol(schedule), schedule, _records(), _leakage())

    assert evidence.reliability_passed
    assert not evidence.confirmatory_qualitative_claims
    assert evidence.gates["agreement"].status == "passed"
    assert evidence.gates["human_calibration"].value == 0
    assert evidence.gates["leakage"].status == "passed"
    assert evidence.blocking_codes == ("paid_or_study_authority_unavailable",)
    store = InMemoryArtifactStore()
    artifact = write_panel_gate_evidence(store, evidence)
    assert store.open_verified(artifact) == evidence.canonical_bytes


def test_reliability_is_order_invariant_and_does_not_average_away_failed_agreement() -> None:
    schedule = _schedule()
    records = list(_records())
    records[0] = _record("candidate-a", 0, 1, "family-one", score=0.0)
    evidence = evaluate_panel_reliability(_protocol(schedule), schedule, records, _leakage())
    reversed_evidence = evaluate_panel_reliability(
        _protocol(schedule), schedule, tuple(reversed(records)), _leakage()
    )

    assert evidence.sha256 == reversed_evidence.sha256
    assert evidence.gates["agreement"].status == "failed"
    assert evidence.criterion_agreement["maintainability"].decision.status == "failed"


def test_reliability_fails_closed_for_missing_evidence_leakage_and_homogeneous_rules() -> None:
    schedule = _schedule()
    records = list(_records(diversity="homogeneous"))
    records[-1] = _record(
        "sentinel-a", 3, 3, "family-three", diversity="homogeneous", status="failed"
    )
    evidence = evaluate_panel_reliability(_protocol(schedule), schedule, records, None)

    assert not evidence.reliability_passed
    assert evidence.gates["agreement"].status == "unavailable"
    assert evidence.gates["human_calibration"].status == "unavailable"
    assert evidence.gates["shared_bias"].status == "unavailable"
    assert evidence.gates["leakage"].status == "unavailable"
    assert "panel_records_incomplete" in evidence.blocking_codes


def test_partial_panel_requires_a_stronger_human_calibration_threshold() -> None:
    schedule = _schedule()
    evidence = evaluate_panel_reliability(
        _protocol(schedule, stronger_threshold=0.05),
        schedule,
        _records(diversity="partial", score=0.70),
        _leakage(),
    )

    assert evidence.gates["human_calibration"].status == "failed"
    assert evidence.gates["human_calibration"].threshold == 0.05
