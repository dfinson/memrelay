from __future__ import annotations

import random
from dataclasses import dataclass, replace
from functools import lru_cache

import pytest
from memrelay_eval.analysis.estimands import (
    FrozenEstimand,
    FrozenEstimatorRegistry,
    MissingnessPolicy,
)
from memrelay_eval.analysis.gates import FrozenThresholdPolicy, evaluate_claim
from memrelay_eval.analysis.intervals import SimultaneousInterval
from memrelay_eval.analysis.multiplicity import FrozenClaimFamily, HolmResult
from memrelay_eval.analysis.power import (
    FinalInformationProof,
    FrozenPowerProtocol,
    FrozenSimulationCell,
    PowerEvaluation,
    evaluate_power,
)
from memrelay_eval.analysis.preregistration import (
    SealedClaimProtocol,
    SealedClaimRegistration,
)
from memrelay_eval.domain.errors import AnalysisError

_PROTOCOL = "a" * 64
_PLAN = "b" * 64
_DATASET = "c" * 64
_ENVIRONMENT = "d" * 64
_AUTHORIZATION = "e" * 64
_PANEL = "f" * 64
_SOURCE = "1" * 64
_DERIVATION = "2" * 64
_CATEGORICAL = "3" * 64


@dataclass(frozen=True)
class _Bundle:
    family: FrozenClaimFamily
    thresholds: FrozenThresholdPolicy
    protocol: FrozenPowerProtocol
    seal: SealedClaimProtocol


def _family_policy(
    mode: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[float, ...], str | None]:
    return {
        "reliability": (
            ("EP-PRIM-SUCCESS", "EP-QUAL", "EP-HARM"),
            ("benefit", "benefit", "harm"),
            ("difference", "difference", "difference"),
            (0.05, 0.05, -0.02),
            None,
        ),
        "efficiency": (
            ("EP-SUCC-NI", "EP-QUAL", "EP-COST", "EP-WALL", "EP-HARM"),
            ("non_inferiority", "benefit", "superiority", "superiority", "harm"),
            ("difference", "difference", "ratio", "ratio", "difference"),
            (-0.02, 0.05, 0.90, 0.90, -0.02),
            "cost",
        ),
    }[mode]


def _estimands(
    endpoint_ids: tuple[str, ...], scales: tuple[str, ...], design: str
) -> tuple[FrozenEstimand, ...]:
    resampling = {
        "blocked": ("blocked_randomization", "blocked_randomization", "controlled"),
        "paired": ("paired_sign_flip", "paired_randomization", "controlled"),
        "clustered": ("cluster_randomization", "blocked_randomization", "controlled"),
        "sequence": ("sequence_randomization", "sequence_randomization", "dynamic"),
    }[design]
    return tuple(
        FrozenEstimand(
            estimand_id=f"{endpoint_id.lower()}-{design}",
            version="1.0.0",
            protocol_sha256=_PROTOCOL,
            source_dataset_manifest_sha256=_DATASET,
            assignment_plan_sha256=_PLAN,
            endpoint_id=endpoint_id,
            population_id="primary",
            stratum="product",
            history_mode=resampling[2],
            assignment_mechanism=resampling[1],
            treatment_strategy=(
                "total_policy_sequence_effect"
                if resampling[2] == "dynamic"
                else "controlled_access_effect"
            ),
            intercurrent_event_policy="itt",
            summary_measure=scale,
            treatment_arm="treatment",
            control_arm="control",
            assignment_unit="sequence" if resampling[2] == "dynamic" else "assignment",
            experimental_unit="sequence" if resampling[2] == "dynamic" else "assignment",
            observation_unit="sequence" if resampling[2] == "dynamic" else "assignment",
            resampling_unit="sequence" if resampling[2] == "dynamic" else "assignment",
            clustering_unit="sequence" if resampling[2] == "dynamic" else "assignment",
            analysis_unit="sequence" if resampling[2] == "dynamic" else "assignment",
            resampling_design=resampling[0],
            missingness_policy=MissingnessPolicy(-1.0, 1.0, {"treatment": 0.5, "control": 0.5}),
        )
        for endpoint_id, scale in zip(endpoint_ids, scales, strict=True)
    )


def _cell(
    family: FrozenClaimFamily,
    *,
    design: str = "blocked",
    missingness_rate: float = 0.0,
    invalid: bool = False,
) -> FrozenSimulationCell:
    count_name, count = {
        "blocked": ("block_count", 16),
        "paired": ("pair_count", 256),
        "clustered": ("cluster_count", 32),
        "sequence": ("sequence_count", 32),
    }[design]
    size = len(family.endpoint_ids)
    correlation = tuple(
        tuple(1.0 if row == column else 0.2 for column in range(size)) for row in range(size)
    )
    if invalid:
        correlation = tuple(
            tuple(
                1.0 if row == column else (0.9 if {row, column} != {1, 2} else -0.9)
                for column in range(size)
            )
            for row in range(size)
        )
    values: dict[str, object] = {
        "cell_id": f"{design}-base",
        "seed": 7,
        "endpoint_target_effects": tuple(
            0.25 if scale == "difference" else 0.65 for scale in family.endpoint_scales
        ),
        "endpoint_scales": family.endpoint_scales,
        "endpoint_baselines": tuple(
            0.5 if scale == "difference" else 1.0 for scale in family.endpoint_scales
        ),
        "correlation": correlation,
        "assignment_design": design,
        "estimator": {
            "blocked": "block_adjusted_difference",
            "paired": "paired_difference",
            "clustered": "cluster_robust_difference",
            "sequence": "sequence_adjusted_difference",
        }[design],
        "nuisance_source": "baseline_only",
        "missingness_rate": missingness_rate,
        count_name: count,
    }
    if design == "paired":
        values["pair_correlation"] = 0.25
    if design == "clustered":
        values["cluster_icc"] = 0.1
    if invalid:
        values["invalid_reason"] = "correlation_not_positive_definite"
    return FrozenSimulationCell(**values)


def _family(
    *,
    mode: str,
    registry: FrozenEstimatorRegistry,
    seal_sha256: str,
) -> FrozenClaimFamily:
    endpoint_ids, directions, scales, margins, selection = _family_policy(mode)
    return FrozenClaimFamily(
        family_id=f"{mode}-primary",
        mode=mode,
        protocol_sha256=_PROTOCOL,
        assignment_plan_sha256=_PLAN,
        source_dataset_manifest_sha256=_DATASET,
        estimand_registry_sha256=registry.registry_sha256,
        environment_fingerprint_sha256=_ENVIRONMENT,
        model_role="primary",
        endpoint_ids=endpoint_ids,
        endpoint_directions=directions,
        endpoint_scales=scales,
        endpoint_margins=margins,
        categorical_gate_ids=("evidence",),
        efficiency_selection=selection,
        sealed_claim_protocol_sha256=seal_sha256,
    )


@lru_cache
def _bundle(
    mode: str = "reliability",
    design: str = "blocked",
    missingness_rate: float = 0.0,
    invalid: bool = False,
) -> _Bundle:
    endpoint_ids, _directions, scales, _margins, _selection = _family_policy(mode)
    registry = FrozenEstimatorRegistry(_estimands(endpoint_ids, scales, design))
    provisional_family = _family(mode=mode, registry=registry, seal_sha256="0" * 64)
    provisional_thresholds = FrozenThresholdPolicy(
        _PROTOCOL,
        provisional_family.family_sha256,
        provisional_family.family_registration_sha256,
        "0" * 64,
        panel_gate_sha256=_PANEL,
    )
    cell = _cell(
        provisional_family,
        design=design,
        missingness_rate=missingness_rate,
        invalid=invalid,
    )
    provisional_protocol = FrozenPowerProtocol(
        _PROTOCOL,
        provisional_family,
        _PLAN,
        registry,
        tuple(
            next(item for item in registry.estimands if item.endpoint_id == endpoint_id)
            for endpoint_id in endpoint_ids
        ),
        512,
        10_000,
        (cell,),
        endpoint_ids[0],
    )
    seal = SealedClaimProtocol(
        _PROTOCOL,
        _PLAN,
        registry.registry_sha256,
        _AUTHORIZATION,
        "authorized_before_enrollment",
        (
            SealedClaimRegistration(
                provisional_family.family_id,
                provisional_family.family_registration_sha256,
                provisional_thresholds.threshold_registration_sha256,
                provisional_protocol.power_registration_sha256,
            ),
        ),
    )
    family = _family(
        mode=mode,
        registry=registry,
        seal_sha256=seal.sealed_claim_protocol_sha256,
    )
    thresholds = FrozenThresholdPolicy(
        _PROTOCOL,
        family.family_sha256,
        family.family_registration_sha256,
        seal.sealed_claim_protocol_sha256,
        panel_gate_sha256=_PANEL,
    )
    protocol = FrozenPowerProtocol(
        _PROTOCOL,
        family,
        _PLAN,
        registry,
        tuple(
            next(item for item in registry.estimands if item.endpoint_id == endpoint_id)
            for endpoint_id in endpoint_ids
        ),
        512,
        10_000,
        (cell,),
        endpoint_ids[0],
        seal,
    )
    return _Bundle(family, thresholds, protocol, seal)


@lru_cache
def _evaluation() -> PowerEvaluation:
    return evaluate_power(_bundle().protocol)


def _holm(family: FrozenClaimFamily, endpoint_id: str = "EP-PRIM-SUCCESS") -> HolmResult:
    return HolmResult(
        endpoint_id, 0.01, 0.03, 1, True, "estimated", family.family_sha256, ("holm",)
    )


def _interval(
    family: FrozenClaimFamily,
    endpoint_id: str = "EP-PRIM-SUCCESS",
    point: float = 0.06,
    lower: float = 0.001,
) -> SimultaneousInterval:
    return SimultaneousInterval(
        endpoint_id,
        point,
        lower,
        0.1,
        0.95,
        "holm-compatible",
        family.family_sha256,
        sidedness="two-sided",
    )


def _context(bundle: _Bundle) -> dict[str, object]:
    return {
        "source_sha256": _SOURCE,
        "derivation_sha256": _DERIVATION,
        "power_evaluation": _evaluation(),
        "power_protocol": bundle.protocol,
        "sealed_claim_protocol": bundle.seal,
        "information_proof": FinalInformationProof(
            _PROTOCOL, bundle.protocol.power_sha256, 512, _SOURCE
        ),
        "categorical_gates_sha256": _CATEGORICAL,
    }


def test_power_evidence_is_evaluator_issued_complete_and_exactly_protocol_bound() -> None:
    bundle = _bundle()
    evidence = _evaluation()
    evidence.validate_against(bundle.protocol)
    assert evidence.cells[0].valid_trials == 10_000
    assert evidence.cells[0].successful_trials <= evidence.cells[0].valid_trials

    copied = PowerEvaluation(
        evidence.protocol_sha256,
        evidence.power_sha256,
        evidence.family_sha256,
        evidence.sealed_claim_protocol_sha256,
        evidence.status,
        evidence.worst_case_power,
        evidence.cells,
        evidence.independent_spot_check_sha256,
    )
    with pytest.raises(AnalysisError, match="not evaluator issued"):
        copied.validate_against(bundle.protocol)

    forged_cell = replace(
        evidence.cells[0],
        valid_trials=9_999,
        power=evidence.cells[0].successful_trials / 9_999,
    )
    forged = PowerEvaluation(
        evidence.protocol_sha256,
        evidence.power_sha256,
        evidence.family_sha256,
        evidence.sealed_claim_protocol_sha256,
        evidence.status,
        evidence.worst_case_power,
        (forged_cell,),
        evidence.independent_spot_check_sha256,
    )
    with pytest.raises(AnalysisError, match="not evaluator issued"):
        forged.validate_against(bundle.protocol)


def test_power_simulation_materializes_itt_and_executes_registered_estimator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle()
    from memrelay_eval.analysis import power as power_module

    calls: list[tuple[str, int]] = []
    actual = power_module.estimate_itt

    def spy(table: object, *, permutation_count: int) -> object:
        calls.append((table.estimand.endpoint_id, len(table.observations)))  # type: ignore[attr-defined]
        return actual(table, permutation_count=permutation_count)  # type: ignore[arg-type]

    monkeypatch.setattr(power_module, "estimate_itt", spy)
    successful, valid = power_module._run_trials(
        bundle.protocol,
        bundle.protocol.cells[0],
        power_module._cholesky(bundle.protocol.cells[0].correlation),
        random.Random(12),
        2,
    )
    assert valid == 2
    assert 0 <= successful <= valid
    assert calls == [(endpoint_id, 512) for endpoint_id in bundle.family.endpoint_ids]


def test_missing_simulation_outcomes_remain_itt_and_are_not_dropped() -> None:
    bundle = _bundle(missingness_rate=1.0)
    from memrelay_eval.analysis import power as power_module

    successful, valid = power_module._run_trials(
        bundle.protocol,
        bundle.protocol.cells[0],
        power_module._cholesky(bundle.protocol.cells[0].correlation),
        random.Random(12),
        3,
    )
    assert (successful, valid) == (0, 3)


def test_registered_invalid_power_cell_retains_zero_trials_and_its_reason() -> None:
    bundle = _bundle(invalid=True)
    evidence = evaluate_power(bundle.protocol)
    result = evidence.cells[0]
    assert evidence.status == "blocked"
    assert (
        result.status,
        result.successful_trials,
        result.valid_trials,
        result.power,
        result.reason,
    ) == ("invalid", 0, 0, None, "correlation_not_positive_definite")
    evidence.validate_against(bundle.protocol)


@pytest.mark.parametrize(
    ("design", "resampling"),
    [
        ("blocked", "blocked_randomization"),
        ("paired", "paired_sign_flip"),
        ("clustered", "cluster_randomization"),
        ("sequence", "sequence_randomization"),
    ],
)
def test_power_cells_bind_registered_estimand_designs(design: str, resampling: str) -> None:
    bundle = _bundle(design=design)
    assert {item.resampling_design for item in bundle.protocol.endpoint_estimands} == {resampling}
    assert (
        bundle.protocol.estimator_registry.registry_sha256 == bundle.family.estimand_registry_sha256
    )


def test_claim_gate_rejects_spoofed_power_before_any_decision() -> None:
    bundle = _bundle()
    evidence = _evaluation()
    spoofed = PowerEvaluation(
        evidence.protocol_sha256,
        evidence.power_sha256,
        evidence.family_sha256,
        evidence.sealed_claim_protocol_sha256,
        evidence.status,
        evidence.worst_case_power,
        evidence.cells,
        evidence.independent_spot_check_sha256,
    )
    context = _context(bundle)
    context["power_evaluation"] = spoofed
    with pytest.raises(AnalysisError, match="not evaluator issued"):
        evaluate_claim(
            bundle.family,
            bundle.thresholds,
            _holm(bundle.family),
            _interval(bundle.family),
            claim_type="reliability_benefit",
            **context,
        )


def test_claim_gate_rejects_claims_without_endpoint_specific_power() -> None:
    bundle = _bundle()
    with pytest.raises(AnalysisError, match="power endpoint mismatch"):
        evaluate_claim(
            bundle.family,
            bundle.thresholds,
            _holm(bundle.family, "EP-QUAL"),
            _interval(bundle.family, "EP-QUAL"),
            claim_type="quality_benefit",
            qualitative_scale=(0.0, 1.0),
            **_context(bundle),
        )


def test_seal_rejects_post_outcome_family_threshold_and_cell_relaxation() -> None:
    bundle = _bundle()
    unsealed_family = replace(bundle.family, sealed_claim_protocol_sha256="0" * 64)
    with pytest.raises(AnalysisError, match="family unregistered"):
        bundle.seal.require_family(
            family_id=unsealed_family.family_id,
            protocol_sha256=unsealed_family.protocol_sha256,
            assignment_plan_sha256=unsealed_family.assignment_plan_sha256,
            estimator_registry_sha256=unsealed_family.estimand_registry_sha256,
            family_registration_sha256=unsealed_family.family_registration_sha256,
            sealed_claim_protocol_sha256=unsealed_family.sealed_claim_protocol_sha256,
        )
    with pytest.raises(AnalysisError, match="relaxation"):
        replace(bundle.thresholds, superiority_ratio=0.91)
    unregistered_cell_bundle = _bundle(invalid=True)
    assert (
        unregistered_cell_bundle.protocol.power_registration_sha256
        != bundle.protocol.power_registration_sha256
    )
    with pytest.raises(AnalysisError, match="power unregistered"):
        bundle.seal.require_power(
            family_id=unregistered_cell_bundle.family.family_id,
            family_registration_sha256=unregistered_cell_bundle.family.family_registration_sha256,
            power_registration_sha256=unregistered_cell_bundle.protocol.power_registration_sha256,
            sealed_claim_protocol_sha256=bundle.seal.sealed_claim_protocol_sha256,
        )


def test_reliability_boundaries_remain_strict_under_sealed_evidence() -> None:
    bundle = _bundle()
    context = _context(bundle)
    decision = evaluate_claim(
        bundle.family,
        bundle.thresholds,
        _holm(bundle.family),
        _interval(bundle.family, point=0.05, lower=0.0001),
        claim_type="reliability_benefit",
        **context,
    )
    assert decision.status in {"pass", "estimation-only"}
    equal_bound = evaluate_claim(
        bundle.family,
        bundle.thresholds,
        _holm(bundle.family),
        _interval(bundle.family, lower=0.0),
        claim_type="reliability_benefit",
        **context,
    )
    assert equal_bound.status in {"fail", "estimation-only"}
