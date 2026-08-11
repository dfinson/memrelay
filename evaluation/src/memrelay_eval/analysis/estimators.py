"""Assignment-aligned ITT construction, exact resampling, and bounded sensitivities."""

from __future__ import annotations

import itertools
import math
import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean, stdev

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import AnalysisError

from .estimands import FrozenEstimand, FrozenEstimatorRegistry


@dataclass(frozen=True, slots=True)
class AssignmentDisclosure:
    """Analysis-lock disclosure of an opaque assignment's registered design position."""

    assignment_id: str
    arm: str
    analysis_unit_id: str
    block_id: str
    cluster_id: str
    resampling_unit_id: str
    model_role: str
    assignment_plan_sha256: str
    pair_id: str | None = None
    pairing_is_registered_and_fresh: bool = False
    run_order: int | None = None
    concurrency_slot: str | None = None
    quota_state: str | None = None
    throttle_state: str | None = None
    provider_time_bucket: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.assignment_id,
            self.arm,
            self.analysis_unit_id,
            self.block_id,
            self.cluster_id,
            self.resampling_unit_id,
            self.model_role,
            self.assignment_plan_sha256,
        )
        if any(not isinstance(value, str) or not value for value in required):
            raise AnalysisError("assignment_disclosure_incomplete")
        if self.pairing_is_registered_and_fresh and not self.pair_id:
            raise AnalysisError("registered_pair_identity_missing")
        if self.run_order is not None and self.run_order < 0:
            raise AnalysisError("run_order_invalid")

    def to_document(self) -> dict[str, object]:
        return {
            "assignment_id": self.assignment_id,
            "arm": self.arm,
            "analysis_unit_id": self.analysis_unit_id,
            "block_id": self.block_id,
            "cluster_id": self.cluster_id,
            "resampling_unit_id": self.resampling_unit_id,
            "model_role": self.model_role,
            "assignment_plan_sha256": self.assignment_plan_sha256,
            "pair_id": self.pair_id,
            "pairing_is_registered_and_fresh": self.pairing_is_registered_and_fresh,
            "run_order": self.run_order,
            "concurrency_slot": self.concurrency_slot,
            "quota_state": self.quota_state,
            "throttle_state": self.throttle_state,
            "provider_time_bucket": self.provider_time_bucket,
        }


@dataclass(frozen=True, slots=True)
class AssignmentAnalysisLock:
    """A canonical analysis-lock disclosure tied to the registry's sealed assignment plan."""

    assignment_plan_sha256: str
    disclosures: tuple[AssignmentDisclosure, ...]

    def __post_init__(self) -> None:
        if not _is_sha256(self.assignment_plan_sha256):
            raise AnalysisError("assignment_analysis_lock_invalid")
        disclosures = tuple(sorted(self.disclosures, key=lambda item: item.assignment_id))
        if not disclosures or len({item.assignment_id for item in disclosures}) != len(disclosures):
            raise AnalysisError("assignment_analysis_lock_invalid")
        if any(item.assignment_plan_sha256 != self.assignment_plan_sha256 for item in disclosures):
            raise AnalysisError("assignment_plan_disclosure_conflict")
        object.__setattr__(self, "disclosures", disclosures)

    @property
    def lock_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "assignment_analysis_lock",
            "assignment_plan_sha256": self.assignment_plan_sha256,
            "disclosures": [item.to_document() for item in self.disclosures],
        }


@dataclass(frozen=True, slots=True)
class IttObservation:
    """One retained assigned unit, including absent or excluded endpoint evidence."""

    assignment_id: str
    analysis_unit_id: str
    arm: str
    task_id: str
    sequence_id: str
    repository_id: str
    environment_fingerprint_sha256: str
    terminal_kind: str
    inclusion_status: str
    attrition_status: str
    exposure_status: str
    contamination_status: str
    outcome_status: str
    numeric_value: float | None
    outcome_id: str | None
    disclosure: AssignmentDisclosure

    @property
    def is_observed(self) -> bool:
        return self.numeric_value is not None

    def to_document(self) -> dict[str, object]:
        return {
            "assignment_id": self.assignment_id,
            "analysis_unit_id": self.analysis_unit_id,
            "arm": self.arm,
            "task_id": self.task_id,
            "sequence_id": self.sequence_id,
            "repository_id": self.repository_id,
            "environment_fingerprint_sha256": self.environment_fingerprint_sha256,
            "terminal_kind": self.terminal_kind,
            "inclusion_status": self.inclusion_status,
            "attrition_status": self.attrition_status,
            "exposure_status": self.exposure_status,
            "contamination_status": self.contamination_status,
            "outcome_status": self.outcome_status,
            "numeric_value": self.numeric_value,
            "outcome_id": self.outcome_id,
            "disclosure": self.disclosure.to_document(),
        }


@dataclass(frozen=True, slots=True)
class IttTable:
    """Immutable primary denominator and its endpoint left join."""

    estimand: FrozenEstimand
    registry_sha256: str
    source_dataset_manifest_sha256: str
    assignment_analysis_lock_sha256: str
    observations: tuple[IttObservation, ...]

    @property
    def table_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "estimand_fingerprint": self.estimand.fingerprint,
            "registry_sha256": self.registry_sha256,
            "source_dataset_manifest_sha256": self.source_dataset_manifest_sha256,
            "assignment_analysis_lock_sha256": self.assignment_analysis_lock_sha256,
            "observations": [item.to_document() for item in self.observations],
        }


class IttTableBuilder:
    """Build complete ITT tables only by left-joining outcomes onto assignments."""

    def build(
        self,
        registry: FrozenEstimatorRegistry,
        *,
        estimand_id: str,
        version: str,
        source_dataset_manifest_sha256: str,
        assigned_units: Iterable[Mapping[str, object]],
        eligible_outcomes: Iterable[Mapping[str, object]],
        assignment_analysis_lock: AssignmentAnalysisLock,
    ) -> IttTable:
        assigned = tuple(assigned_units)
        if not assigned:
            raise AnalysisError("assigned_unit_denominator_empty")
        protocol_values = {str(_required(row, "protocol_sha256")) for row in assigned}
        if len(protocol_values) != 1:
            raise AnalysisError("assigned_unit_protocol_conflict")
        estimand = registry.require(
            estimand_id,
            version,
            protocol_sha256=next(iter(protocol_values)),
            source_dataset_manifest_sha256=source_dataset_manifest_sha256,
        )
        if assignment_analysis_lock.assignment_plan_sha256 != estimand.assignment_plan_sha256:
            raise AnalysisError("assignment_plan_disclosure_conflict")
        disclosure_by_assignment = _disclosures_by_assignment(assignment_analysis_lock.disclosures)
        assigned_by_id = _rows_by_assignment(assigned, "assigned_unit_duplicate")
        if set(assigned_by_id) != set(disclosure_by_assignment):
            raise AnalysisError("assignment_lineage_incomplete")
        outcomes_by_assignment = _endpoint_outcomes(eligible_outcomes, estimand.endpoint_id)
        observations = tuple(
            self._observation(
                assigned_by_id[assignment_id],
                disclosure_by_assignment[assignment_id],
                outcomes_by_assignment.get(assignment_id),
                estimand,
            )
            for assignment_id in sorted(assigned_by_id)
        )
        _validate_units(observations, estimand)
        return IttTable(
            estimand=estimand,
            registry_sha256=registry.registry_sha256,
            source_dataset_manifest_sha256=source_dataset_manifest_sha256,
            assignment_analysis_lock_sha256=assignment_analysis_lock.lock_sha256,
            observations=observations,
        )

    def _observation(
        self,
        assigned: Mapping[str, object],
        disclosure: AssignmentDisclosure,
        outcome: Mapping[str, object] | None,
        estimand: FrozenEstimand,
    ) -> IttObservation:
        assignment_id = str(_required(assigned, "assignment_id"))
        required_matches = {
            "protocol_sha256": estimand.protocol_sha256,
            "population_id": estimand.population_id,
            "stratum": estimand.stratum,
            "history_mode": estimand.history_mode,
        }
        if any(str(_required(assigned, key)) != value for key, value in required_matches.items()):
            raise AnalysisError("assigned_unit_estimand_mismatch")
        if str(_required(assigned, "analysis_unit_id")) != disclosure.analysis_unit_id:
            raise AnalysisError("assignment_analysis_unit_mismatch")
        status = str(_required(assigned, "outcome_measurement_status"))
        if outcome is not None:
            _validate_outcome(outcome, assigned, estimand)
            if status != "eligible":
                raise AnalysisError("outcome_status_authority_conflict")
            numeric_value = outcome.get("numeric_value")
            if not isinstance(numeric_value, (int, float)) or not math.isfinite(
                float(numeric_value)
            ):
                raise AnalysisError("numeric_endpoint_required")
            value = float(numeric_value)
            outcome_id = str(_required(outcome, "outcome_id"))
        else:
            if status == "eligible":
                raise AnalysisError("eligible_outcome_missing")
            value = None
            outcome_id = None
        return IttObservation(
            assignment_id=assignment_id,
            analysis_unit_id=disclosure.analysis_unit_id,
            arm=disclosure.arm,
            task_id=str(_required(assigned, "task_id")),
            sequence_id=str(_required(assigned, "sequence_id")),
            repository_id=str(_required(assigned, "repository_id")),
            environment_fingerprint_sha256=str(
                _required(assigned, "environment_fingerprint_sha256")
            ),
            terminal_kind=str(_required(assigned, "terminal_kind")),
            inclusion_status=str(_required(assigned, "inclusion_status")),
            attrition_status=str(_required(assigned, "attrition_status")),
            exposure_status=_normalized_exposure(str(_required(assigned, "exposure_status"))),
            contamination_status=str(_required(assigned, "contamination_status")),
            outcome_status=status,
            numeric_value=value,
            outcome_id=outcome_id,
            disclosure=disclosure,
        )


@dataclass(frozen=True, slots=True)
class EffectEstimate:
    """A descriptive estimate only; Story 5.4 owns multiplicity and claims."""

    point_estimate: float | None
    p_value: float | None
    independent_n: int
    status: str
    reason: str | None = None

    def to_document(self) -> dict[str, object]:
        return {
            "point_estimate": self.point_estimate,
            "p_value": self.p_value,
            "independent_n": self.independent_n,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class EstimatorDecisionRecord:
    """The immutable, non-claim outcome of one exact frozen-estimator execution."""

    source_itt_sha256: str
    estimand_fingerprint: str
    registry_sha256: str
    estimate: EffectEstimate

    @property
    def decision_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "frozen_estimator_decision",
            "source_itt_sha256": self.source_itt_sha256,
            "estimand_fingerprint": self.estimand_fingerprint,
            "registry_sha256": self.registry_sha256,
            "estimate": self.estimate.to_document(),
            "multiplicity_owner": "story_5_4",
        }


@dataclass(frozen=True, slots=True)
class SensitivityBounds:
    worst_case: float
    best_case: float
    pattern_mixture: float


@dataclass(frozen=True, slots=True)
class ClusterSensitivity:
    cr2_standard_error: float | None
    degrees_of_freedom: int
    wild_cluster_p_value: float | None
    status: str


@dataclass(frozen=True, slots=True)
class CmhSensitivity:
    common_odds_ratio: float | None
    chi_square: float | None
    p_value: float | None
    degrees_of_freedom: int
    status: str


@dataclass(frozen=True, slots=True)
class GeeSensitivity:
    point_estimate: float | None
    robust_standard_error: float | None
    t_degrees_of_freedom: int
    p_value: float | None
    status: str


@dataclass(frozen=True, slots=True)
class GlmmSensitivity:
    log_odds_ratio: float | None
    random_effect_variance: float | None
    standard_error: float | None
    status: str


def estimate_itt(table: IttTable, *, permutation_count: int = 10_000) -> EffectEstimate:
    """Estimate only when every retained independent unit has its primary value."""
    _validate_table_integrity(table)
    if any(not item.is_observed for item in table.observations):
        return EffectEstimate(
            point_estimate=None,
            p_value=None,
            independent_n=len(table.observations),
            status="indeterminate",
            reason="primary_outcome_missing_requires_frozen_bounds",
        )
    point = _effect(table.observations, table.estimand)
    p_value = _randomization_p_value(table, permutation_count)
    return EffectEstimate(point, p_value, len(table.observations), "estimated")


def estimator_decision(
    table: IttTable, *, permutation_count: int = 10_000
) -> EstimatorDecisionRecord:
    """Bind one result to its immutable table and frozen registry without making a claim."""
    return EstimatorDecisionRecord(
        source_itt_sha256=table.table_sha256,
        estimand_fingerprint=table.estimand.fingerprint,
        registry_sha256=table.registry_sha256,
        estimate=estimate_itt(table, permutation_count=permutation_count),
    )


def estimate_itt_bounds(table: IttTable) -> SensitivityBounds:
    """Return frozen worst, best, and arm-specific pattern-mixture ITT sensitivities."""
    _validate_table_integrity(table)
    policy = table.estimand.missingness_policy
    values: dict[str, list[float]] = {
        "worst_case": [],
        "best_case": [],
        "pattern_mixture": [],
    }
    for observation in table.observations:
        if observation.numeric_value is not None:
            current = observation.numeric_value
            for bucket in values.values():
                bucket.append(current)
            continue
        if observation.arm == table.estimand.treatment_arm:
            values["worst_case"].append(policy.lower_bound)
            values["best_case"].append(policy.upper_bound)
        else:
            values["worst_case"].append(policy.upper_bound)
            values["best_case"].append(policy.lower_bound)
        fraction = policy.pattern_mixture_by_arm[observation.arm]
        values["pattern_mixture"].append(
            policy.lower_bound + fraction * (policy.upper_bound - policy.lower_bound)
        )
    observations = table.observations
    return SensitivityBounds(
        worst_case=_effect_with_values(observations, values["worst_case"], table.estimand),
        best_case=_effect_with_values(observations, values["best_case"], table.estimand),
        pattern_mixture=_effect_with_values(
            observations, values["pattern_mixture"], table.estimand
        ),
    )


def cluster_sensitivity(
    table: IttTable, *, bootstrap_replicates: int = 2_000, seed: int = 0
) -> ClusterSensitivity:
    """Fail closed until a qualified CR2 and residual wild-bootstrap backend is registered."""
    _validate_table_integrity(table)
    if bootstrap_replicates < 1 or seed < 0:
        raise AnalysisError("wild_cluster_bootstrap_configuration_invalid")
    return ClusterSensitivity(None, 0, None, "indeterminate")


def cmh_sensitivity(table: IttTable) -> CmhSensitivity:
    """Run the frozen CMH binary-outcome sensitivity within registered task blocks."""
    _validate_table_integrity(table)
    if any(item.numeric_value not in {0.0, 1.0} for item in table.observations):
        return CmhSensitivity(None, None, None, 1, "indeterminate")
    blocks: dict[str, list[IttObservation]] = defaultdict(list)
    for item in table.observations:
        blocks[item.disclosure.block_id].append(item)
    numerator = 0.0
    variance = 0.0
    odds_numerator = 0.0
    odds_denominator = 0.0
    for block in blocks.values():
        treatment = [item for item in block if item.arm == table.estimand.treatment_arm]
        control = [item for item in block if item.arm == table.estimand.control_arm]
        if not treatment or not control:
            raise AnalysisError("assignment_block_arm_support_invalid")
        successes_treatment = sum(item.numeric_value for item in treatment)
        successes_control = sum(item.numeric_value for item in control)
        total = len(block)
        successes = successes_treatment + successes_control
        treated_count = len(treatment)
        control_count = len(control)
        numerator += successes_treatment - treated_count * successes / total
        if total > 1:
            variance += (
                treated_count
                * control_count
                * successes
                * (total - successes)
                / (total * total * (total - 1))
            )
        odds_numerator += successes_treatment * (control_count - successes_control) / total
        odds_denominator += (treated_count - successes_treatment) * successes_control / total
    if variance <= 0.0 or odds_denominator == 0.0:
        return CmhSensitivity(None, None, None, 1, "indeterminate")
    chi_square = numerator * numerator / variance
    return CmhSensitivity(
        common_odds_ratio=odds_numerator / odds_denominator,
        chi_square=chi_square,
        p_value=math.erfc(math.sqrt(chi_square / 2.0)),
        degrees_of_freedom=1,
        status="estimated",
    )


def gee_sensitivity(table: IttTable) -> GeeSensitivity:
    """Use independent assignment clusters for the identity-link GEE sensitivity."""
    _validate_table_integrity(table)
    if any(not item.is_observed for item in table.observations):
        return GeeSensitivity(None, None, 0, None, "indeterminate")
    cluster_effects = _cluster_effects(table)
    if len(cluster_effects) < 2:
        raise AnalysisError("cluster_count_insufficient")
    estimate = fmean(cluster_effects)
    standard_error = stdev(cluster_effects) / math.sqrt(len(cluster_effects))
    if standard_error == 0.0:
        return GeeSensitivity(estimate, 0.0, len(cluster_effects) - 1, 0.0, "estimated")
    z_score = abs(estimate / standard_error)
    return GeeSensitivity(
        estimate,
        standard_error,
        len(cluster_effects) - 1,
        math.erfc(z_score / math.sqrt(2.0)),
        "estimated" if len(cluster_effects) >= 20 else "indeterminate",
    )


def glmm_sensitivity(table: IttTable) -> GlmmSensitivity:
    """Fit a deterministic random-effects log-odds sensitivity over assignment clusters.

    This is an analysis sensitivity only. Separation or sparse binary data remains
    indeterminate instead of receiving a continuity correction.
    """
    _validate_table_integrity(table)
    if any(item.numeric_value not in {0.0, 1.0} for item in table.observations):
        return GlmmSensitivity(None, None, None, "indeterminate")
    cluster_estimates: list[tuple[float, float]] = []
    clusters: dict[str, list[IttObservation]] = defaultdict(list)
    for item in table.observations:
        clusters[item.disclosure.cluster_id].append(item)
    for values in clusters.values():
        treatment = [
            item.numeric_value for item in values if item.arm == table.estimand.treatment_arm
        ]
        control = [item.numeric_value for item in values if item.arm == table.estimand.control_arm]
        if not treatment or not control:
            raise AnalysisError("assignment_cluster_arm_support_invalid")
        successes_treatment = sum(treatment)
        successes_control = sum(control)
        failures_treatment = len(treatment) - successes_treatment
        failures_control = len(control) - successes_control
        if (
            min(
                successes_treatment,
                successes_control,
                failures_treatment,
                failures_control,
            )
            == 0
        ):
            return GlmmSensitivity(None, None, None, "indeterminate")
        log_odds = math.log(
            successes_treatment * failures_control / (failures_treatment * successes_control)
        )
        variance = (
            1 / successes_treatment
            + 1 / failures_treatment
            + 1 / successes_control
            + 1 / failures_control
        )
        cluster_estimates.append((log_odds, variance))
    if len(cluster_estimates) < 2:
        raise AnalysisError("cluster_count_insufficient")
    fixed_weights = [1 / variance for _, variance in cluster_estimates]
    fixed_effect = sum(
        weight * effect
        for weight, (effect, _) in zip(fixed_weights, cluster_estimates, strict=True)
    ) / sum(fixed_weights)
    q_statistic = sum(
        weight * (effect - fixed_effect) ** 2
        for weight, (effect, _) in zip(fixed_weights, cluster_estimates, strict=True)
    )
    correction = sum(fixed_weights) - sum(weight * weight for weight in fixed_weights) / sum(
        fixed_weights
    )
    tau_squared = max(0.0, (q_statistic - (len(cluster_estimates) - 1)) / correction)
    random_weights = [1 / (variance + tau_squared) for _, variance in cluster_estimates]
    log_odds = sum(
        weight * effect
        for weight, (effect, _) in zip(random_weights, cluster_estimates, strict=True)
    ) / sum(random_weights)
    return GlmmSensitivity(
        log_odds_ratio=log_odds,
        random_effect_variance=tau_squared,
        standard_error=math.sqrt(1 / sum(random_weights)),
        status="estimated" if len(cluster_estimates) >= 20 else "indeterminate",
    )


def _rows_by_assignment(
    rows: Sequence[Mapping[str, object]], duplicate_code: str
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for row in rows:
        assignment_id = str(_required(row, "assignment_id"))
        if assignment_id in result:
            raise AnalysisError(duplicate_code)
        result[assignment_id] = row
    return result


def _disclosures_by_assignment(
    disclosures: Sequence[AssignmentDisclosure],
) -> dict[str, AssignmentDisclosure]:
    result = {item.assignment_id: item for item in disclosures}
    if len(result) != len(disclosures):
        raise AnalysisError("assignment_disclosure_duplicate")
    return result


def _endpoint_outcomes(
    outcomes: Iterable[Mapping[str, object]], endpoint_id: str
) -> dict[str, Mapping[str, object]]:
    matching = [row for row in outcomes if str(_required(row, "endpoint_id")) == endpoint_id]
    return _rows_by_assignment(matching, "authorized_outcome_not_sole")


def _validate_outcome(
    outcome: Mapping[str, object], assigned: Mapping[str, object], estimand: FrozenEstimand
) -> None:
    for key in (
        "assignment_id",
        "analysis_unit_id",
        "protocol_sha256",
        "population_id",
        "stratum",
        "history_mode",
        "run_id",
        "attempt_id",
        "reconciliation_sha256",
    ):
        if _required(outcome, key) != _required(assigned, key):
            raise AnalysisError("eligible_outcome_lineage_conflict")
    if str(_required(outcome, "endpoint_id")) != estimand.endpoint_id:
        raise AnalysisError("eligible_outcome_endpoint_conflict")
    if str(_required(outcome, "outcome_kind")) != "numeric":
        raise AnalysisError("numeric_endpoint_required")


def _validate_units(observations: Sequence[IttObservation], estimand: FrozenEstimand) -> None:
    if {item.arm for item in observations} != {estimand.treatment_arm, estimand.control_arm}:
        raise AnalysisError("assigned_arm_support_invalid")
    if len({item.analysis_unit_id for item in observations}) != len(observations):
        raise AnalysisError("analysis_unit_not_independent")
    if estimand.history_mode == "dynamic":
        for item in observations:
            if (
                item.analysis_unit_id != item.sequence_id
                or item.disclosure.resampling_unit_id != item.sequence_id
            ):
                raise AnalysisError("dynamic_sequence_unit_mismatch")
    elif any(not item.disclosure.resampling_unit_id for item in observations):
        raise AnalysisError("resampling_unit_missing")


def _validate_table_integrity(table: IttTable) -> None:
    if not table.observations:
        raise AnalysisError("itt_table_empty")
    if table.source_dataset_manifest_sha256 != table.estimand.source_dataset_manifest_sha256:
        raise AnalysisError("analysis_lineage_incomplete")
    _validate_units(table.observations, table.estimand)
    analysis_strata = {
        (item.environment_fingerprint_sha256, item.disclosure.model_role)
        for item in table.observations
    }
    if len(analysis_strata) != 1:
        raise AnalysisError("changed_fingerprint_or_model_role_requires_separate_analysis")


def _cluster_effects(table: IttTable) -> list[float]:
    clusters: dict[str, list[IttObservation]] = defaultdict(list)
    for observation in table.observations:
        clusters[observation.disclosure.cluster_id].append(observation)
    return [_effect(values, table.estimand) for _, values in sorted(clusters.items())]


def _effect(observations: Sequence[IttObservation], estimand: FrozenEstimand) -> float:
    if any(item.numeric_value is None for item in observations):
        raise AnalysisError("primary_outcome_missing_requires_frozen_bounds")
    return _effect_with_values(
        observations,
        [float(item.numeric_value) for item in observations if item.numeric_value is not None],
        estimand,
    )


def _effect_with_values(
    observations: Sequence[IttObservation], values: Sequence[float], estimand: FrozenEstimand
) -> float:
    if len(observations) != len(values):
        raise AnalysisError("outcome_value_alignment_invalid")
    by_arm_task: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for observation, value in zip(observations, values, strict=True):
        by_arm_task[observation.arm][observation.task_id].append(value)
    treatment_tasks = set(by_arm_task[estimand.treatment_arm])
    control_tasks = set(by_arm_task[estimand.control_arm])
    if not treatment_tasks or treatment_tasks != control_tasks:
        raise AnalysisError("equal_task_weight_support_invalid")
    treatment = fmean(
        fmean(by_arm_task[estimand.treatment_arm][task]) for task in sorted(treatment_tasks)
    )
    control = fmean(
        fmean(by_arm_task[estimand.control_arm][task]) for task in sorted(control_tasks)
    )
    if estimand.summary_measure == "difference":
        return treatment - control
    if control == 0.0:
        raise AnalysisError("ratio_comparator_zero")
    return treatment / control


def _randomization_p_value(table: IttTable, permutation_count: int) -> float:
    if permutation_count < 1:
        raise AnalysisError("permutation_count_invalid")
    observations = table.observations
    if table.estimand.resampling_design == "paired_sign_flip":
        permutations = _pair_sign_permutations(observations, table.estimand)
    else:
        permutations = _block_permutations(observations, table.estimand, permutation_count)
    observed_statistic = _randomization_statistic(table.observations, table.estimand)
    values = list(permutations)
    if not values:
        raise AnalysisError("randomization_distribution_empty")
    return (sum(abs(value) >= abs(observed_statistic) for value in values) + 1) / (len(values) + 1)


def _pair_sign_permutations(
    observations: Sequence[IttObservation], estimand: FrozenEstimand
) -> Iterable[float]:
    pairs: dict[str, list[IttObservation]] = defaultdict(list)
    for observation in observations:
        disclosure = observation.disclosure
        if not disclosure.pairing_is_registered_and_fresh or not disclosure.pair_id:
            raise AnalysisError("pairing_not_registered_and_fresh")
        pairs[disclosure.pair_id].append(observation)
    differences: list[float] = []
    for pair in pairs.values():
        if len(pair) != 2 or {item.arm for item in pair} != {
            estimand.treatment_arm,
            estimand.control_arm,
        }:
            raise AnalysisError("genuine_pair_required")
        treatment = next(item for item in pair if item.arm == estimand.treatment_arm)
        control = next(item for item in pair if item.arm == estimand.control_arm)
        if treatment.numeric_value is None or control.numeric_value is None:
            raise AnalysisError("primary_outcome_missing_requires_frozen_bounds")
        differences.append(
            _pair_statistic(treatment.numeric_value, control.numeric_value, estimand)
        )
    if len(differences) > 16:
        rng = random.Random(0)
        return (
            fmean(value if rng.getrandbits(1) else -value for value in differences)
            for _ in range(10_000)
        )
    return (
        fmean(sign * value for sign, value in zip(signs, differences, strict=True))
        for signs in itertools.product((-1, 1), repeat=len(differences))
    )


def _block_permutations(
    observations: Sequence[IttObservation], estimand: FrozenEstimand, permutation_count: int
) -> Iterable[float]:
    blocks: dict[str, list[IttObservation]] = defaultdict(list)
    for observation in observations:
        blocks[observation.disclosure.block_id].append(observation)
    layouts: list[list[tuple[IttObservation, ...]]] = []
    for block in blocks.values():
        count = sum(item.arm == estimand.treatment_arm for item in block)
        if count == 0 or count == len(block):
            raise AnalysisError("assignment_block_arm_support_invalid")
        layouts.append(list(itertools.combinations(tuple(block), count)))
    combinations_count = math.prod(len(layout) for layout in layouts)

    def statistic(chosen: Sequence[tuple[IttObservation, ...]]) -> float:
        reassigned: list[IttObservation] = []
        for block, treated in zip(blocks.values(), chosen, strict=True):
            treated_ids = {item.assignment_id for item in treated}
            for item in block:
                arm = (
                    estimand.treatment_arm
                    if item.assignment_id in treated_ids
                    else estimand.control_arm
                )
                reassigned.append(
                    IttObservation(
                        assignment_id=item.assignment_id,
                        analysis_unit_id=item.analysis_unit_id,
                        arm=arm,
                        task_id=item.task_id,
                        sequence_id=item.sequence_id,
                        repository_id=item.repository_id,
                        environment_fingerprint_sha256=item.environment_fingerprint_sha256,
                        terminal_kind=item.terminal_kind,
                        inclusion_status=item.inclusion_status,
                        attrition_status=item.attrition_status,
                        exposure_status=item.exposure_status,
                        contamination_status=item.contamination_status,
                        outcome_status=item.outcome_status,
                        numeric_value=item.numeric_value,
                        outcome_id=item.outcome_id,
                        disclosure=item.disclosure,
                    )
                )
        return _randomization_statistic(reassigned, estimand)

    if combinations_count <= permutation_count:
        return (statistic(item) for item in itertools.product(*layouts))
    rng = random.Random(0)
    return (statistic([rng.choice(layout) for layout in layouts]) for _ in range(permutation_count))


def _normalized_exposure(value: str) -> str:
    return "exposed" if value in {"ambiguous", "unknown", "contradictory"} else value


def _randomization_statistic(
    observations: Sequence[IttObservation], estimand: FrozenEstimand
) -> float:
    effect = _effect(observations, estimand)
    if estimand.summary_measure == "difference":
        return effect
    if effect <= 0.0:
        raise AnalysisError("ratio_statistic_nonpositive")
    return math.log(effect)


def _pair_statistic(treatment: float, control: float, estimand: FrozenEstimand) -> float:
    if estimand.summary_measure == "difference":
        return treatment - control
    if treatment <= 0.0 or control <= 0.0:
        raise AnalysisError("ratio_comparator_zero")
    return math.log(treatment / control)


def _required(row: Mapping[str, object], key: str) -> object:
    value = row.get(key)
    if value is None or value == "":
        raise AnalysisError("analysis_lineage_incomplete")
    return value


def _is_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and value.isascii()
        and all(character in "0123456789abcdef" for character in value)
    )
