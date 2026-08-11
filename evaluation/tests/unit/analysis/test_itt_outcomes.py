from __future__ import annotations

import pytest
from memrelay_eval.analysis.diagnostics import build_diagnostics
from memrelay_eval.analysis.estimands import (
    FrozenEstimand,
    FrozenEstimatorRegistry,
    MissingnessPolicy,
)
from memrelay_eval.analysis.estimators import (
    AssignmentAnalysisLock,
    AssignmentDisclosure,
    IttTableBuilder,
    estimate_itt,
    estimate_itt_bounds,
)
from memrelay_eval.domain.errors import AnalysisError

_DATASET = "a" * 64
_PROTOCOL = "b" * 64
_ASSIGNMENT_PLAN = "c" * 64


def _estimand(**changes: object) -> FrozenEstimand:
    values: dict[str, object] = {
        "estimand_id": "quality-itt",
        "version": "1.0.0",
        "protocol_sha256": _PROTOCOL,
        "source_dataset_manifest_sha256": _DATASET,
        "assignment_plan_sha256": _ASSIGNMENT_PLAN,
        "endpoint_id": "quality",
        "population_id": "primary",
        "stratum": "product",
        "history_mode": "controlled",
        "assignment_mechanism": "blocked_randomization",
        "treatment_strategy": "controlled_access_effect",
        "intercurrent_event_policy": "retain_all_assigned_units",
        "summary_measure": "difference",
        "treatment_arm": "memory",
        "control_arm": "control",
        "assignment_unit": "task",
        "experimental_unit": "task",
        "observation_unit": "attempt",
        "resampling_unit": "task",
        "clustering_unit": "task",
        "analysis_unit": "task",
        "resampling_design": "blocked_randomization",
        "missingness_policy": MissingnessPolicy(0.0, 1.0, {"memory": 0.25, "control": 0.75}),
    }
    values.update(changes)
    return FrozenEstimand(**values)


def _assigned(
    assignment_id: str,
    *,
    task_id: str,
    outcome_status: str = "eligible",
    terminal_kind: str = "succeeded",
    attrition: str = "complete",
    exposure: str = "exposed",
    fingerprint: str = "f" * 64,
) -> dict[str, object]:
    return {
        "assignment_id": assignment_id,
        "analysis_unit_id": assignment_id,
        "run_id": "run-" + assignment_id,
        "attempt_id": "attempt-" + assignment_id,
        "protocol_sha256": _PROTOCOL,
        "population_id": "primary",
        "stratum": "product",
        "history_mode": "controlled",
        "task_id": task_id,
        "sequence_id": "sequence-" + task_id,
        "repository_id": "repo",
        "environment_fingerprint_sha256": fingerprint,
        "terminal_kind": terminal_kind,
        "inclusion_status": "included" if outcome_status == "eligible" else "excluded",
        "attrition_status": attrition,
        "exposure_status": exposure,
        "contamination_status": "isolated",
        "outcome_measurement_status": outcome_status,
        "reconciliation_sha256": "d" * 64,
    }


def _outcome(assignment_id: str, value: float) -> dict[str, object]:
    return {
        "assignment_id": assignment_id,
        "analysis_unit_id": assignment_id,
        "run_id": "run-" + assignment_id,
        "attempt_id": "attempt-" + assignment_id,
        "protocol_sha256": _PROTOCOL,
        "population_id": "primary",
        "stratum": "product",
        "history_mode": "controlled",
        "endpoint_id": "quality",
        "outcome_id": "outcome-" + assignment_id,
        "outcome_kind": "numeric",
        "numeric_value": value,
        "reconciliation_sha256": "d" * 64,
    }


def _disclosure(
    assignment_id: str, arm: str, task_id: str, **changes: object
) -> AssignmentDisclosure:
    values: dict[str, object] = {
        "assignment_id": assignment_id,
        "arm": arm,
        "analysis_unit_id": assignment_id,
        "block_id": "block-" + task_id,
        "cluster_id": "cluster-" + task_id,
        "resampling_unit_id": "resampling-" + task_id,
        "model_role": "primary",
        "assignment_plan_sha256": _ASSIGNMENT_PLAN,
    }
    values.update(changes)
    return AssignmentDisclosure(**values)


def _lock(*disclosures: AssignmentDisclosure) -> AssignmentAnalysisLock:
    return AssignmentAnalysisLock(_ASSIGNMENT_PLAN, disclosures)


def _table(*, missing: bool = False, changed_fingerprint: bool = False):
    assigned = (
        _assigned("a1", task_id="one"),
        _assigned("a2", task_id="one"),
        _assigned(
            "a3",
            task_id="two",
            outcome_status="missing" if missing else "eligible",
            terminal_kind="timed_out" if missing else "succeeded",
            attrition="attrited" if missing else "complete",
            exposure="ambiguous" if missing else "exposed",
            fingerprint="e" * 64 if changed_fingerprint else "f" * 64,
        ),
        _assigned("a4", task_id="two"),
    )
    outcomes = (_outcome("a1", 0.8), _outcome("a2", 0.2), _outcome("a4", 0.3))
    if not missing:
        outcomes += (_outcome("a3", 0.9),)
    disclosures = (
        _disclosure("a1", "memory", "one"),
        _disclosure("a2", "control", "one"),
        _disclosure("a3", "memory", "two"),
        _disclosure("a4", "control", "two"),
    )
    return IttTableBuilder().build(
        FrozenEstimatorRegistry((_estimand(),)),
        estimand_id="quality-itt",
        version="1.0.0",
        source_dataset_manifest_sha256=_DATASET,
        assigned_units=assigned,
        eligible_outcomes=outcomes,
        assignment_analysis_lock=_lock(*disclosures),
    )


def test_itt_left_joins_full_assigned_denominator_and_never_uses_completers() -> None:
    table = _table(missing=True)

    assert len(table.observations) == 4
    assert table.observations[2].numeric_value is None
    assert table.observations[2].terminal_kind == "timed_out"
    assert table.observations[2].exposure_status == "exposed"
    assert estimate_itt(table).status == "indeterminate"
    assert estimate_itt_bounds(table).worst_case == pytest.approx(0.15)


def test_itt_rejects_missing_assignment_lineage_and_favorable_outcome_substitution() -> None:
    with pytest.raises(AnalysisError, match="assignment lineage incomplete"):
        IttTableBuilder().build(
            FrozenEstimatorRegistry((_estimand(),)),
            estimand_id="quality-itt",
            version="1.0.0",
            source_dataset_manifest_sha256=_DATASET,
            assigned_units=(_assigned("a1", task_id="one"),),
            eligible_outcomes=(_outcome("a1", 0.8),),
            assignment_analysis_lock=_lock(_disclosure("unmatched", "memory", "one")),
        )
    with pytest.raises(AnalysisError, match="authorized outcome not sole"):
        IttTableBuilder().build(
            FrozenEstimatorRegistry((_estimand(),)),
            estimand_id="quality-itt",
            version="1.0.0",
            source_dataset_manifest_sha256=_DATASET,
            assigned_units=(_assigned("a1", task_id="one"),),
            eligible_outcomes=(_outcome("a1", 0.8), _outcome("a1", 0.9)),
            assignment_analysis_lock=_lock(_disclosure("a1", "memory", "one")),
        )


def test_balance_diagnostics_preserve_changed_fingerprint_and_model_role_strata() -> None:
    report = build_diagnostics(_table(missing=True, changed_fingerprint=True))

    assert report.fingerprint_model_role_strata == {
        "e" * 64 + ":primary": 1,
        "f" * 64 + ":primary": 3,
    }
    attrition = next(item for item in report.diagnostics if item.field == "attrition_status")
    assert attrition.counts_by_arm["memory"]["attrited"] == 1


def test_effect_estimation_rejects_mixed_fingerprint_or_model_role_strata() -> None:
    with pytest.raises(AnalysisError, match="requires separate analysis"):
        estimate_itt(_table(changed_fingerprint=True))
