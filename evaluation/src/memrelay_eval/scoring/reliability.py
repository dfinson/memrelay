"""Deterministic, fail-closed reliability gates for blinded judge panels."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import JudgePanelConformanceError
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.evidence.required import require_unpaid_conformance_ports
from memrelay_eval.scoring.blinding import LeakageConformance
from memrelay_eval.scoring.calibration import (
    AGREEMENT_THRESHOLD,
    HUMAN_CALIBRATION_MAE_THRESHOLD,
    FrozenPanelQualificationProtocol,
)
from memrelay_eval.scoring.rubric import JUDGE_CRITERIA, FrozenPanelSchedule, JudgeRecord

PANEL_GATE_SCHEMA_VERSION = "1.0.0"
_GATE_STATUSES = frozenset({"passed", "failed", "unavailable", "not_required"})


@dataclass(frozen=True, slots=True)
class GateDecision:
    """One categorical, non-overridable gate with its observed value and frozen rule."""

    status: str
    value: float | None
    threshold: float | None
    comparison: str

    def __post_init__(self) -> None:
        if (
            self.status not in _GATE_STATUSES
            or self.comparison not in {"minimum", "maximum", "none"}
            or (self.value is not None and not isinstance(self.value, (int, float)))
            or (self.threshold is not None and not isinstance(self.threshold, (int, float)))
        ):
            raise JudgePanelConformanceError("panel_gate_decision_invalid")

    def document(self) -> dict[str, object]:
        return {
            "status": self.status,
            "value": self.value,
            "threshold": self.threshold,
            "comparison": self.comparison,
        }


@dataclass(frozen=True, slots=True)
class CriterionAgreement:
    """Criterion-specific agreement; no cross-criterion average can mask a failure."""

    metric: str
    decision: GateDecision

    def document(self) -> dict[str, object]:
        return {"metric": self.metric, **self.decision.document()}


@dataclass(frozen=True, slots=True)
class PanelGateEvidence:
    """Canonical evidence for a reliability decision, never a numeric replacement score."""

    qualification_protocol_sha256: str
    panel_protocol_sha256: str | None
    records_sha256: str
    diversity_label: str | None
    criterion_agreement: Mapping[str, CriterionAgreement]
    gates: Mapping[str, GateDecision]
    per_judge_drift: Mapping[str, float | None]
    per_criterion_calibration_mae: Mapping[str, float | None]
    blocking_codes: tuple[str, ...]
    schema_version: str = PANEL_GATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            self.schema_version != PANEL_GATE_SCHEMA_VERSION
            or len(self.qualification_protocol_sha256) != 64
            or len(self.records_sha256) != 64
            or (self.panel_protocol_sha256 is not None and len(self.panel_protocol_sha256) != 64)
            or self.diversity_label not in {None, "diverse", "partial", "homogeneous"}
            or set(self.criterion_agreement) != set(JUDGE_CRITERIA)
        ):
            raise JudgePanelConformanceError("panel_gate_evidence_invalid")
        object.__setattr__(
            self, "criterion_agreement", MappingProxyType(dict(self.criterion_agreement))
        )
        object.__setattr__(self, "gates", MappingProxyType(dict(self.gates)))
        object.__setattr__(self, "per_judge_drift", MappingProxyType(dict(self.per_judge_drift)))
        object.__setattr__(
            self,
            "per_criterion_calibration_mae",
            MappingProxyType(dict(self.per_criterion_calibration_mae)),
        )
        object.__setattr__(self, "blocking_codes", tuple(sorted(set(self.blocking_codes))))

    @property
    def reliability_passed(self) -> bool:
        return all(
            decision.status in {"passed", "not_required"} for decision in self.gates.values()
        )

    @property
    def confirmatory_qualitative_claims(self) -> bool:
        """Story 3.3 fake evidence cannot authorize paid or study qualitative claims."""
        return False

    def document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "qualification_protocol_sha256": self.qualification_protocol_sha256,
            "panel_protocol_sha256": self.panel_protocol_sha256,
            "records_sha256": self.records_sha256,
            "panel_diversity": self.diversity_label,
            "criterion_agreement": {
                criterion: self.criterion_agreement[criterion].document()
                for criterion in JUDGE_CRITERIA
            },
            "gates": {name: decision.document() for name, decision in sorted(self.gates.items())},
            "per_judge_drift": dict(sorted(self.per_judge_drift.items())),
            "per_criterion_calibration_mae": {
                criterion: self.per_criterion_calibration_mae[criterion]
                for criterion in JUDGE_CRITERIA
            },
            "reliability_passed": self.reliability_passed,
            "confirmatory_qualitative_claims": self.confirmatory_qualitative_claims,
            "artifact_authority": "unpaid_conformance",
            "blocking_codes": list(self.blocking_codes),
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.document())

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes).hexdigest()


def evaluate_panel_reliability(
    protocol: FrozenPanelQualificationProtocol,
    schedule: FrozenPanelSchedule,
    records: Sequence[JudgeRecord],
    leakage: LeakageConformance | None,
    *,
    direct_leak_categories: Sequence[str] = (),
) -> PanelGateEvidence:
    """Evaluate every frozen gate without provider calls or outcome-dependent tuning."""
    protocol.bind_schedule(schedule)
    ordered_records = tuple(
        sorted(records, key=lambda record: (record.candidate_id, record.judge_slot))
    )
    records_sha256 = sha256(
        canonical_bytes([record.document() for record in ordered_records])
    ).hexdigest()
    by_item = _group_records(ordered_records)
    diversity_label, panel_protocol_sha256, structural_codes = _panel_identity(
        schedule, ordered_records, by_item
    )
    complete = not structural_codes
    agreement = _criterion_agreement(protocol, schedule, by_item, complete)
    gates: dict[str, GateDecision] = {
        "agreement": _all_criteria_gate(agreement),
        "human_calibration": _unavailable(),
        "drift": _unavailable(),
        "duplicate": _unavailable(),
        "sentinel": _unavailable(),
        "leave_one_out": _unavailable(),
        "family_sensitivity": _unavailable(),
        "shared_bias": GateDecision("not_required", None, None, "none"),
        "leakage": _leakage_gate(leakage, direct_leak_categories),
    }
    calibration, calibration_per_criterion = _calibration_gate(
        protocol, schedule, by_item, diversity_label, complete
    )
    gates["human_calibration"] = calibration
    gates["drift"], per_judge_drift = _drift_gate(protocol, schedule, by_item, complete)
    gates["duplicate"] = _duplicate_gate(protocol, by_item, complete)
    gates["sentinel"] = _sentinel_gate(protocol, schedule, by_item, complete)
    gates["leave_one_out"] = _leave_one_out_gate(protocol, schedule, by_item, complete)
    gates["family_sensitivity"] = _family_sensitivity_gate(protocol, schedule, by_item, complete)
    if diversity_label in {"partial", "homogeneous"}:
        gates["shared_bias"] = _shared_bias_gate(protocol, schedule, by_item, complete)
    blocking_codes = list(structural_codes)
    blocking_codes.extend(
        f"panel_gate_{name}_{decision.status}"
        for name, decision in gates.items()
        if decision.status not in {"passed", "not_required"}
    )
    blocking_codes.append("paid_or_study_authority_unavailable")
    return PanelGateEvidence(
        qualification_protocol_sha256=protocol.sha256,
        panel_protocol_sha256=panel_protocol_sha256,
        records_sha256=records_sha256,
        diversity_label=diversity_label,
        criterion_agreement=agreement,
        gates=gates,
        per_judge_drift=per_judge_drift,
        per_criterion_calibration_mae=calibration_per_criterion,
        blocking_codes=tuple(blocking_codes),
    )


def write_panel_gate_evidence(store: ArtifactStorePort, evidence: PanelGateEvidence) -> ArtifactRef:
    """Persist the exact canonical gate evidence through the inherited fake CAS only."""
    require_unpaid_conformance_ports(store)
    return store.put_bytes(
        evidence.canonical_bytes,
        media_type="application/json",
        classification="unpaid_conformance",
    )


def _group_records(records: Sequence[JudgeRecord]) -> dict[str, tuple[JudgeRecord, ...]]:
    grouped: dict[str, list[JudgeRecord]] = defaultdict(list)
    for record in records:
        grouped[record.candidate_id].append(record)
    return {
        candidate_id: tuple(sorted(candidate_records, key=lambda record: record.judge_slot))
        for candidate_id, candidate_records in grouped.items()
    }


def _panel_identity(
    schedule: FrozenPanelSchedule,
    records: Sequence[JudgeRecord],
    by_item: Mapping[str, Sequence[JudgeRecord]],
) -> tuple[str | None, str | None, tuple[str, ...]]:
    codes: list[str] = []
    expected = set(schedule.item_ids)
    if set(by_item) != expected:
        codes.append("panel_records_do_not_match_sealed_schedule")
    if len(records) != len(schedule.item_ids) * 3:
        codes.append("panel_records_not_exactly_three_per_item")
    panel_protocols = {record.panel_protocol_sha256 for record in records}
    diversity = {record.diversity_label for record in records}
    if len(panel_protocols) != 1:
        codes.append("panel_protocol_evidence_unstable")
    if len(diversity) != 1:
        codes.append("panel_diversity_evidence_unstable")
    for item_id in schedule.item_ids:
        item_records = by_item.get(item_id, ())
        if (
            len(item_records) != 3
            or {record.judge_slot for record in item_records} != {1, 2, 3}
            or any(record.status != "completed" for record in item_records)
        ):
            codes.append("panel_records_incomplete")
            break
    return (
        next(iter(diversity)) if len(diversity) == 1 else None,
        next(iter(panel_protocols)) if len(panel_protocols) == 1 else None,
        tuple(sorted(set(codes))),
    )


def _criterion_agreement(
    protocol: FrozenPanelQualificationProtocol,
    schedule: FrozenPanelSchedule,
    by_item: Mapping[str, Sequence[JudgeRecord]],
    complete: bool,
) -> dict[str, CriterionAgreement]:
    result: dict[str, CriterionAgreement] = {}
    for criterion in JUDGE_CRITERIA:
        metric = protocol.metric_for(criterion)
        if not complete:
            result[criterion] = CriterionAgreement(metric, _unavailable())
            continue
        matrix = [
            [record.criteria[criterion].score for record in by_item[item_id]]
            for item_id in schedule.item_ids
        ]
        value = _icc(matrix) if metric == "icc" else _weighted_kappa(matrix)
        result[criterion] = CriterionAgreement(
            metric,
            GateDecision(
                "passed" if value >= AGREEMENT_THRESHOLD else "failed",
                value,
                AGREEMENT_THRESHOLD,
                "minimum",
            ),
        )
    return result


def _calibration_gate(
    protocol: FrozenPanelQualificationProtocol,
    schedule: FrozenPanelSchedule,
    by_item: Mapping[str, Sequence[JudgeRecord]],
    diversity_label: str | None,
    complete: bool,
) -> tuple[GateDecision, dict[str, float | None]]:
    empty = dict.fromkeys(JUDGE_CRITERIA)
    threshold = HUMAN_CALIBRATION_MAE_THRESHOLD
    if diversity_label in {"partial", "homogeneous"}:
        threshold = protocol.stronger_human_calibration_mae_threshold
    if not complete or threshold is None:
        return _unavailable(), empty
    errors: dict[str, list[float]] = {criterion: [] for criterion in JUDGE_CRITERIA}
    for item_id in schedule.human_calibration_ids:
        label = protocol.gold_label(item_id)
        if label is None:
            return _unavailable(), empty
        for record in by_item[item_id]:
            for criterion in JUDGE_CRITERIA:
                errors[criterion].append(
                    abs(record.criteria[criterion].score - label.criteria[criterion])
                )
    per_criterion = {
        criterion: _mean(errors[criterion]) if errors[criterion] else None
        for criterion in JUDGE_CRITERIA
    }
    values = [value for value in per_criterion.values() if value is not None]
    value = _mean(values) if len(values) == len(JUDGE_CRITERIA) else None
    if value is None:
        return _unavailable(), per_criterion
    return (
        GateDecision("passed" if value <= threshold else "failed", value, threshold, "maximum"),
        per_criterion,
    )


def _drift_gate(
    protocol: FrozenPanelQualificationProtocol,
    schedule: FrozenPanelSchedule,
    by_item: Mapping[str, Sequence[JudgeRecord]],
    complete: bool,
) -> tuple[GateDecision, dict[str, float | None]]:
    threshold = protocol.pass_rules.drift_max_abs_change
    window = protocol.drift_window_size
    per_judge = {str(slot): None for slot in (1, 2, 3)}
    if not complete or threshold is None or window is None or len(schedule.item_ids) < 2 * window:
        return _unavailable(), per_judge
    ordered = sorted(
        (record for records in by_item.values() for record in records),
        key=lambda record: (record.schedule_position, record.judge_slot),
    )
    for slot in (1, 2, 3):
        slot_records = [record for record in ordered if record.judge_slot == slot]
        changes = [
            abs(
                _mean([record.criteria[criterion].score for record in slot_records[:window]])
                - _mean([record.criteria[criterion].score for record in slot_records[-window:]])
            )
            for criterion in JUDGE_CRITERIA
        ]
        per_judge[str(slot)] = max(changes)
    value = max(value for value in per_judge.values() if value is not None)
    return (
        GateDecision("passed" if value <= threshold else "failed", value, threshold, "maximum"),
        per_judge,
    )


def _duplicate_gate(
    protocol: FrozenPanelQualificationProtocol,
    by_item: Mapping[str, Sequence[JudgeRecord]],
    complete: bool,
) -> GateDecision:
    threshold = protocol.pass_rules.duplicate_max_abs_difference
    if not complete or threshold is None:
        return _unavailable()
    differences = [
        abs(
            by_item[duplicate][slot - 1].criteria[criterion].score
            - by_item[original][slot - 1].criteria[criterion].score
        )
        for duplicate, original in protocol.duplicate_pairs
        for slot in (1, 2, 3)
        for criterion in JUDGE_CRITERIA
    ]
    if not differences:
        return _unavailable()
    value = max(differences)
    return GateDecision("passed" if value <= threshold else "failed", value, threshold, "maximum")


def _sentinel_gate(
    protocol: FrozenPanelQualificationProtocol,
    schedule: FrozenPanelSchedule,
    by_item: Mapping[str, Sequence[JudgeRecord]],
    complete: bool,
) -> GateDecision:
    threshold = protocol.pass_rules.sentinel_max_mae
    if not complete or threshold is None:
        return _unavailable()
    errors: list[float] = []
    for item_id in schedule.sentinel_ids:
        label = protocol.gold_label(item_id)
        if label is None:
            return _unavailable()
        errors.extend(
            abs(record.criteria[criterion].score - label.criteria[criterion])
            for record in by_item[item_id]
            for criterion in JUDGE_CRITERIA
        )
    if not errors:
        return _unavailable()
    value = _mean(errors)
    return GateDecision("passed" if value <= threshold else "failed", value, threshold, "maximum")


def _leave_one_out_gate(
    protocol: FrozenPanelQualificationProtocol,
    schedule: FrozenPanelSchedule,
    by_item: Mapping[str, Sequence[JudgeRecord]],
    complete: bool,
) -> GateDecision:
    threshold = protocol.pass_rules.leave_one_out_max_abs_change
    if not complete or threshold is None:
        return _unavailable()
    values: list[float] = []
    all_records = [record for records in by_item.values() for record in records]
    for criterion in JUDGE_CRITERIA:
        full = _mean([record.criteria[criterion].score for record in all_records])
        for slot in (1, 2, 3):
            without = [
                record.criteria[criterion].score
                for record in all_records
                if record.judge_slot != slot
            ]
            values.append(abs(full - _mean(without)))
    value = max(values)
    return GateDecision("passed" if value <= threshold else "failed", value, threshold, "maximum")


def _family_sensitivity_gate(
    protocol: FrozenPanelQualificationProtocol,
    schedule: FrozenPanelSchedule,
    by_item: Mapping[str, Sequence[JudgeRecord]],
    complete: bool,
) -> GateDecision:
    threshold = protocol.pass_rules.family_sensitivity_max_abs_difference
    if not complete or threshold is None:
        return _unavailable()
    records = [record for records in by_item.values() for record in records]
    generator = [
        record for record in records if record.model_family == protocol.generator_model_family
    ]
    others = [
        record for record in records if record.model_family != protocol.generator_model_family
    ]
    if not generator or not others:
        return _unavailable()
    differences = [
        abs(
            _mean([record.criteria[criterion].score for record in generator])
            - _mean([record.criteria[criterion].score for record in others])
        )
        for criterion in JUDGE_CRITERIA
    ]
    value = max(differences)
    return GateDecision("passed" if value <= threshold else "failed", value, threshold, "maximum")


def _shared_bias_gate(
    protocol: FrozenPanelQualificationProtocol,
    schedule: FrozenPanelSchedule,
    by_item: Mapping[str, Sequence[JudgeRecord]],
    complete: bool,
) -> GateDecision:
    threshold = protocol.pass_rules.shared_bias_max_abs_difference
    if not complete or threshold is None:
        return _unavailable()
    by_family: dict[str, list[JudgeRecord]] = defaultdict(list)
    for records in by_item.values():
        for record in records:
            by_family[record.model_family].append(record)
    if len(by_family) < 2:
        return _unavailable()
    values = [
        abs(
            _mean([record.criteria[criterion].score for record in family_records])
            - _mean(
                [
                    record.criteria[criterion].score
                    for other_family, records in by_family.items()
                    if other_family != family
                    for record in records
                ]
            )
        )
        for criterion in JUDGE_CRITERIA
        for family, family_records in by_family.items()
    ]
    value = max(values)
    return GateDecision("passed" if value <= threshold else "failed", value, threshold, "maximum")


def _leakage_gate(
    leakage: LeakageConformance | None, direct_leak_categories: Sequence[str]
) -> GateDecision:
    if leakage is None:
        return _unavailable()
    categories = set(direct_leak_categories).union(leakage.direct_leak_categories)
    if categories:
        return GateDecision("failed", leakage.upper_auc_95, 0.60, "maximum")
    return GateDecision(
        "passed" if leakage.upper_auc_95 <= 0.60 else "failed",
        leakage.upper_auc_95,
        0.60,
        "maximum",
    )


def _all_criteria_gate(
    agreement: Mapping[str, CriterionAgreement],
) -> GateDecision:
    decisions = [agreement[criterion].decision for criterion in JUDGE_CRITERIA]
    if any(decision.status == "unavailable" for decision in decisions):
        return _unavailable()
    value = min(decision.value for decision in decisions if decision.value is not None)
    return GateDecision(
        "passed" if all(decision.status == "passed" for decision in decisions) else "failed",
        value,
        AGREEMENT_THRESHOLD,
        "minimum",
    )


def _unavailable() -> GateDecision:
    return GateDecision("unavailable", None, None, "none")


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise JudgePanelConformanceError("panel_metric_requires_values")
    return sum(values) / len(values)


def _icc(matrix: Sequence[Sequence[float]]) -> float:
    targets = len(matrix)
    raters = len(matrix[0]) if matrix else 0
    if targets < 2 or raters < 2 or any(len(row) != raters for row in matrix):
        raise JudgePanelConformanceError("panel_icc_requires_complete_matrix")
    grand = _mean([value for row in matrix for value in row])
    target_means = [_mean(row) for row in matrix]
    rater_means = [_mean([row[index] for row in matrix]) for index in range(raters)]
    ms_target = raters * sum((value - grand) ** 2 for value in target_means) / (targets - 1)
    ms_rater = targets * sum((value - grand) ** 2 for value in rater_means) / (raters - 1)
    residual = sum(
        (value - target_means[row_index] - rater_means[column_index] + grand) ** 2
        for row_index, row in enumerate(matrix)
        for column_index, value in enumerate(row)
    )
    ms_error = residual / ((targets - 1) * (raters - 1))
    denominator = ms_target + (raters - 1) * ms_error + raters * (ms_rater - ms_error) / targets
    if math.isclose(denominator, 0.0, abs_tol=1e-12):
        return 1.0 if all(value == matrix[0][0] for row in matrix for value in row) else 0.0
    return max(-1.0, min(1.0, (ms_target - ms_error) / denominator))


def _weighted_kappa(matrix: Sequence[Sequence[float]]) -> float:
    if len(matrix) < 2 or not matrix or len(matrix[0]) < 2:
        raise JudgePanelConformanceError("panel_kappa_requires_complete_matrix")
    values = [
        _pairwise_weighted_kappa([row[left] for row in matrix], [row[right] for row in matrix])
        for left in range(len(matrix[0]))
        for right in range(left + 1, len(matrix[0]))
    ]
    return _mean(values)


def _pairwise_weighted_kappa(left: Sequence[float], right: Sequence[float]) -> float:
    bins = 10
    observed = [[0.0 for _ in range(bins + 1)] for _ in range(bins + 1)]
    for first, second in zip(left, right, strict=True):
        observed[round(first * bins)][round(second * bins)] += 1
    total = len(left)
    rows = [sum(row) for row in observed]
    columns = [sum(observed[row][column] for row in range(bins + 1)) for column in range(bins + 1)]
    observed_weight = (
        sum(
            (1 - ((row - column) / bins) ** 2) * observed[row][column]
            for row in range(bins + 1)
            for column in range(bins + 1)
        )
        / total
    )
    expected_weight = sum(
        (1 - ((row - column) / bins) ** 2) * rows[row] * columns[column] / (total * total)
        for row in range(bins + 1)
        for column in range(bins + 1)
    )
    if math.isclose(1 - expected_weight, 0.0, abs_tol=1e-12):
        return 1.0 if observed_weight == 1.0 else 0.0
    return max(-1.0, min(1.0, (observed_weight - expected_weight) / (1 - expected_weight)))
