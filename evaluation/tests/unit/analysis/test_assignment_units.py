from __future__ import annotations

import pytest
from memrelay_eval.analysis.estimands import FrozenEstimatorRegistry
from memrelay_eval.analysis.estimators import (
    IttTableBuilder,
    cmh_sensitivity,
    estimate_itt,
    gee_sensitivity,
    glmm_sensitivity,
)
from memrelay_eval.domain.errors import AnalysisError
from tests.unit.analysis.test_itt_outcomes import (
    _DATASET,
    _assigned,
    _disclosure,
    _estimand,
    _lock,
    _outcome,
)


def test_paired_estimator_requires_real_registered_pairs() -> None:
    estimand = _estimand(
        assignment_mechanism="paired_randomization",
        resampling_design="paired_sign_flip",
    )
    disclosures = (_disclosure("a1", "memory", "one"), _disclosure("a2", "control", "one"))
    table = IttTableBuilder().build(
        FrozenEstimatorRegistry((estimand,)),
        estimand_id="quality-itt",
        version="1.0.0",
        source_dataset_manifest_sha256=_DATASET,
        assigned_units=(_assigned("a1", task_id="one"), _assigned("a2", task_id="one")),
        eligible_outcomes=(_outcome("a1", 0.8), _outcome("a2", 0.2)),
        assignment_analysis_lock=_lock(*disclosures),
    )

    with pytest.raises(AnalysisError, match="pairing not registered and fresh"):
        estimate_itt(table)


def test_dynamic_analysis_rejects_episode_as_independent_unit() -> None:
    estimand = _estimand(
        assignment_mechanism="sequence_randomization",
        history_mode="dynamic",
        treatment_strategy="total_policy_sequence_effect",
        assignment_unit="sequence",
        experimental_unit="sequence",
        observation_unit="sequence",
        resampling_unit="sequence",
        clustering_unit="sequence",
        analysis_unit="sequence",
        resampling_design="sequence_randomization",
    )

    with pytest.raises(AnalysisError, match="dynamic sequence unit mismatch"):
        IttTableBuilder().build(
            FrozenEstimatorRegistry((estimand,)),
            estimand_id="quality-itt",
            version="1.0.0",
            source_dataset_manifest_sha256=_DATASET,
            assigned_units=(
                dict(_assigned("a1", task_id="one"), history_mode="dynamic"),
                dict(_assigned("a2", task_id="one"), history_mode="dynamic"),
            ),
            eligible_outcomes=(
                dict(_outcome("a1", 0.8), history_mode="dynamic"),
                dict(_outcome("a2", 0.2), history_mode="dynamic"),
            ),
            assignment_analysis_lock=_lock(
                _disclosure("a1", "memory", "one"),
                _disclosure("a2", "control", "one"),
            ),
        )


def test_binary_cmh_gee_and_glmm_sensitivities_are_cluster_aware() -> None:
    estimand = _estimand()
    assignment_ids = tuple(f"a{number}" for number in range(1, 9))
    assigned = tuple(
        _assigned(assignment_id, task_id=f"task-{position // 2}")
        for position, assignment_id in enumerate(assignment_ids)
    )
    outcomes = tuple(
        _outcome(assignment_id, float(value))
        for assignment_id, value in zip(assignment_ids, (1, 0, 0, 1, 1, 0, 0, 1), strict=True)
    )
    disclosures = tuple(
        _disclosure(
            assignment_id,
            "memory" if position % 2 == 0 else "control",
            f"task-{position // 2}",
            block_id="block-one" if position < 4 else "block-two",
            cluster_id="cluster-one" if position < 4 else "cluster-two",
            resampling_unit_id=f"resampling-{position}",
        )
        for position, assignment_id in enumerate(assignment_ids)
    )
    table = IttTableBuilder().build(
        FrozenEstimatorRegistry((estimand,)),
        estimand_id="quality-itt",
        version="1.0.0",
        source_dataset_manifest_sha256=_DATASET,
        assigned_units=assigned,
        eligible_outcomes=outcomes,
        assignment_analysis_lock=_lock(*disclosures),
    )

    assert cmh_sensitivity(table).status == "estimated"
    assert gee_sensitivity(table).status == "estimated"
    assert glmm_sensitivity(table).status == "indeterminate"
