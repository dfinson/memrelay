from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from memrelay_eval.scoring.blinding import LeakageConformance
from memrelay_eval.scoring.calibration import (
    FrozenPanelPassRules,
    FrozenPanelQualificationProtocol,
    HumanGoldLabel,
)
from memrelay_eval.scoring.reliability import evaluate_panel_reliability
from memrelay_eval.scoring.rubric import (
    JUDGE_CRITERIA,
    FrozenPanelSchedule,
    JudgeCriterionScore,
    JudgeLimits,
    JudgeRecord,
)


def test_panel_gate_evidence_matches_the_versioned_schema() -> None:
    schedule = FrozenPanelSchedule(
        ("candidate-a",),
        ("calibration-a",),
        ("duplicate-a",),
        ("sentinel-a",),
        "a" * 64,
        JudgeLimits(10, 1, 1, 1, 12, 120, 12, 12, 12),
    )
    labels = dict.fromkeys(JUDGE_CRITERIA, 0.5)
    protocol = FrozenPanelQualificationProtocol(
        schedule_sha256=schedule.sha256,
        gold_label_provenance_sha256="b" * 64,
        human_gold_labels=(
            HumanGoldLabel("calibration-a", labels),
            HumanGoldLabel("sentinel-a", labels),
        ),
        duplicate_pairs=(("duplicate-a", "candidate-a"),),
        criterion_metrics=tuple((criterion, "weighted_kappa") for criterion in JUDGE_CRITERIA),
        drift_window_size=1,
        pass_rules=FrozenPanelPassRules(0.1, 0.1, 0.1, 0.1, 0.1, 0.1),
        generator_model_family="family-one",
        sensitivity_rule_version="family-mean-difference-v1",
    )
    citation = f"artifact://blinded/{'c' * 64}"
    records = tuple(
        JudgeRecord(
            candidate_id=item_id,
            view_sha256="d" * 64,
            panel_protocol_sha256="e" * 64,
            schedule_position=position,
            judge_slot=slot,
            model_id=f"judge-{slot}",
            model_family=f"family-{slot}",
            diversity_label="diverse",
            requires_stronger_calibration=False,
            runtime_lock_sha256="f" * 64,
            model_lock_sha256="0" * 64,
            rubric_sha256="1" * 64,
            system_prompt_sha256="2" * 64,
            tools_sha256="3" * 64,
            decoding_controls_sha256="4" * 64,
            status="completed",
            criteria={
                criterion: JudgeCriterionScore(0.5, 0.1, (citation,))
                for criterion in JUDGE_CRITERIA
            },
        )
        for position, item_id in enumerate(schedule.item_ids)
        for slot in (1, 2, 3)
    )
    evidence = evaluate_panel_reliability(
        protocol, schedule, records, LeakageConformance("5" * 64, 0.5, 0.6, ())
    )
    schema_path = Path(__file__).parents[3] / "schemas" / "panel-gate.schema.json"

    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(
        evidence.document()
    )
