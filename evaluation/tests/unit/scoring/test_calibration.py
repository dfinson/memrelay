from __future__ import annotations

import pytest
from memrelay_eval.domain.errors import JudgePanelConformanceError
from memrelay_eval.scoring.calibration import (
    FrozenPanelPassRules,
    FrozenPanelQualificationProtocol,
    HumanGoldLabel,
)
from memrelay_eval.scoring.rubric import JUDGE_CRITERIA, FrozenPanelSchedule, JudgeLimits


def _schedule() -> FrozenPanelSchedule:
    return FrozenPanelSchedule(
        ("candidate-a",),
        ("calibration-a",),
        ("duplicate-a",),
        ("sentinel-a",),
        "a" * 64,
        JudgeLimits(10, 1, 1, 1, 12, 120, 12, 12, 12),
    )


def _protocol(schedule: FrozenPanelSchedule, **changes: object) -> FrozenPanelQualificationProtocol:
    values = dict.fromkeys(JUDGE_CRITERIA, 0.5)
    arguments: dict[str, object] = {
        "schedule_sha256": schedule.sha256,
        "gold_label_provenance_sha256": "b" * 64,
        "human_gold_labels": (
            HumanGoldLabel("calibration-a", values),
            HumanGoldLabel("sentinel-a", values),
        ),
        "duplicate_pairs": (("duplicate-a", "candidate-a"),),
        "criterion_metrics": tuple((criterion, "icc") for criterion in JUDGE_CRITERIA),
        "drift_window_size": 1,
        "pass_rules": FrozenPanelPassRules(0.1, 0.1, 0.1, 0.1, 0.1, 0.1),
        "generator_model_family": "family-one",
        "sensitivity_rule_version": "family-mean-difference-v1",
    }
    arguments.update(changes)
    return FrozenPanelQualificationProtocol(**arguments)


def test_qualification_protocol_binds_labels_pairs_metrics_and_schedule() -> None:
    schedule = _schedule()
    protocol = _protocol(schedule)

    protocol.bind_schedule(schedule)
    assert protocol.sha256 == _protocol(schedule).sha256
    assert protocol.metric_for("maintainability") == "icc"


def test_qualification_protocol_rejects_unsealed_calibration_authority() -> None:
    schedule = _schedule()
    with pytest.raises(JudgePanelConformanceError, match="qualification protocol invalid"):
        _protocol(schedule, criterion_metrics=(("maintainability", "icc"),))

    with pytest.raises(JudgePanelConformanceError, match="qualification schedule mismatch"):
        _protocol(schedule, schedule_sha256="c" * 64).bind_schedule(schedule)

    with pytest.raises(JudgePanelConformanceError, match="qualification protocol invalid"):
        _protocol(schedule, stronger_human_calibration_mae_threshold=0.10)
