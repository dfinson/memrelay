"""Frozen inputs for deterministic panel-reliability qualification."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.errors import JudgePanelConformanceError
from memrelay_eval.scoring.rubric import JUDGE_CRITERIA, FrozenPanelSchedule

PANEL_QUALIFICATION_PROTOCOL_VERSION = "1.0.0"
AGREEMENT_THRESHOLD = 0.70
HUMAN_CALIBRATION_MAE_THRESHOLD = 0.10
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_AGREEMENT_METRICS = frozenset({"icc", "weighted_kappa"})


def _normalized_score(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise JudgePanelConformanceError(code)
    return float(value)


@dataclass(frozen=True, slots=True)
class HumanGoldLabel:
    """One sealed human-labeled item used for calibration or sentinel checks."""

    item_id: str
    criteria: Mapping[str, float]

    def __post_init__(self) -> None:
        values = dict(self.criteria)
        if not self.item_id or set(values) != set(JUDGE_CRITERIA):
            raise JudgePanelConformanceError("panel_human_gold_label_invalid")
        object.__setattr__(
            self,
            "criteria",
            MappingProxyType(
                {
                    criterion: _normalized_score(
                        values[criterion], "panel_human_gold_label_invalid"
                    )
                    for criterion in JUDGE_CRITERIA
                }
            ),
        )

    def document(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "criteria": {criterion: self.criteria[criterion] for criterion in JUDGE_CRITERIA},
        }


@dataclass(frozen=True, slots=True)
class FrozenPanelPassRules:
    """Protocol-supplied non-primary reliability gates; absent rules never pass."""

    drift_max_abs_change: float | None
    duplicate_max_abs_difference: float | None
    sentinel_max_mae: float | None
    leave_one_out_max_abs_change: float | None
    family_sensitivity_max_abs_difference: float | None
    shared_bias_max_abs_difference: float | None

    def __post_init__(self) -> None:
        for value in (
            self.drift_max_abs_change,
            self.duplicate_max_abs_difference,
            self.sentinel_max_mae,
            self.leave_one_out_max_abs_change,
            self.family_sensitivity_max_abs_difference,
            self.shared_bias_max_abs_difference,
        ):
            if value is not None:
                _normalized_score(value, "panel_pass_rule_invalid")

    def document(self) -> dict[str, float | None]:
        return {
            "drift_max_abs_change": self.drift_max_abs_change,
            "duplicate_max_abs_difference": self.duplicate_max_abs_difference,
            "sentinel_max_mae": self.sentinel_max_mae,
            "leave_one_out_max_abs_change": self.leave_one_out_max_abs_change,
            "family_sensitivity_max_abs_difference": self.family_sensitivity_max_abs_difference,
            "shared_bias_max_abs_difference": self.shared_bias_max_abs_difference,
        }


@dataclass(frozen=True, slots=True)
class FrozenPanelQualificationProtocol:
    """Entire pre-outcome calibration authority bound to one sealed panel schedule."""

    schedule_sha256: str
    gold_label_provenance_sha256: str
    human_gold_labels: tuple[HumanGoldLabel, ...]
    duplicate_pairs: tuple[tuple[str, str], ...]
    criterion_metrics: tuple[tuple[str, str], ...]
    drift_window_size: int | None
    pass_rules: FrozenPanelPassRules
    generator_model_family: str
    sensitivity_rule_version: str
    stronger_human_calibration_mae_threshold: float | None = None
    version: str = PANEL_QUALIFICATION_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        labels = tuple(self.human_gold_labels)
        metrics = tuple(self.criterion_metrics)
        pairs = tuple((left, right) for left, right in self.duplicate_pairs)
        if (
            self.version != PANEL_QUALIFICATION_PROTOCOL_VERSION
            or not _SHA256.fullmatch(self.schedule_sha256)
            or not _SHA256.fullmatch(self.gold_label_provenance_sha256)
            or not labels
            or len({label.item_id for label in labels}) != len(labels)
            or {name for name, _ in metrics} != set(JUDGE_CRITERIA)
            or len(metrics) != len(JUDGE_CRITERIA)
            or any(metric not in _AGREEMENT_METRICS for _, metric in metrics)
            or len(set(pairs)) != len(pairs)
            or any(not left or not right or left == right for left, right in pairs)
            or not self.generator_model_family
            or (self.drift_window_size is not None and self.drift_window_size <= 0)
            or (
                self.stronger_human_calibration_mae_threshold is not None
                and not 0
                < self.stronger_human_calibration_mae_threshold
                < HUMAN_CALIBRATION_MAE_THRESHOLD
            )
            or not self.sensitivity_rule_version
        ):
            raise JudgePanelConformanceError("panel_qualification_protocol_invalid")
        object.__setattr__(self, "human_gold_labels", labels)
        object.__setattr__(self, "duplicate_pairs", pairs)
        object.__setattr__(self, "criterion_metrics", metrics)

    def bind_schedule(self, schedule: FrozenPanelSchedule) -> None:
        """Deny a protocol whose corpus, allocation, or order commitment has drifted."""
        if schedule.sha256 != self.schedule_sha256:
            raise JudgePanelConformanceError("panel_qualification_schedule_mismatch")
        item_ids = set(schedule.item_ids)
        labels = {label.item_id for label in self.human_gold_labels}
        if (
            not set(schedule.human_calibration_ids).issubset(labels)
            or not set(schedule.sentinel_ids).issubset(labels)
            or any(
                duplicate not in item_ids or original not in item_ids
                for duplicate, original in self.duplicate_pairs
            )
            or {duplicate for duplicate, _ in self.duplicate_pairs} != set(schedule.duplicate_ids)
        ):
            raise JudgePanelConformanceError("panel_qualification_schedule_items_invalid")

    def gold_label(self, item_id: str) -> HumanGoldLabel | None:
        return next((label for label in self.human_gold_labels if label.item_id == item_id), None)

    def metric_for(self, criterion: str) -> str:
        return dict(self.criterion_metrics)[criterion]

    def document(self) -> dict[str, object]:
        return {
            "version": self.version,
            "schedule_sha256": self.schedule_sha256,
            "gold_label_provenance_sha256": self.gold_label_provenance_sha256,
            "human_gold_labels": [label.document() for label in self.human_gold_labels],
            "duplicate_pairs": [
                {"duplicate_item_id": duplicate, "original_item_id": original}
                for duplicate, original in self.duplicate_pairs
            ],
            "criterion_metrics": {
                criterion: self.metric_for(criterion) for criterion in JUDGE_CRITERIA
            },
            "drift_window_size": self.drift_window_size,
            "pass_rules": self.pass_rules.document(),
            "generator_model_family": self.generator_model_family,
            "stronger_human_calibration_mae_threshold": (
                self.stronger_human_calibration_mae_threshold
            ),
            "sensitivity_rule_version": self.sensitivity_rule_version,
            "agreement_threshold": AGREEMENT_THRESHOLD,
            "human_calibration_mae_threshold": HUMAN_CALIBRATION_MAE_THRESHOLD,
        }

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.document())).hexdigest()
