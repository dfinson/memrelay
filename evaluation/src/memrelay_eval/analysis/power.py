"""Reproducible power simulations over the registered Story 5.3 estimator path."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import AnalysisError

from .estimands import FrozenEstimand, FrozenEstimatorRegistry
from .estimators import (
    AssignmentDisclosure,
    EffectEstimate,
    IttObservation,
    IttTable,
    estimate_itt,
)
from .multiplicity import EndpointPValue, FrozenClaimFamily, holm_fwer
from .preregistration import SealedClaimProtocol

MIN_SIMULATION_TRIALS = 10_000
PRIMARY_INFORMATION_UNITS = 512
POWER_ESTIMATOR_VERSION = "1.0.0"
_SIMULATION_PERMUTATIONS = 64

_DESIGN_ESTIMATORS = {
    "blocked": "block_adjusted_difference",
    "paired": "paired_difference",
    "clustered": "cluster_robust_difference",
    "sequence": "sequence_adjusted_difference",
}
_DESIGN_RESAMPLING = {
    "blocked": "blocked_randomization",
    "paired": "paired_sign_flip",
    "clustered": "cluster_randomization",
    "sequence": "sequence_randomization",
}


def _valid_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value.isascii()
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class FrozenSimulationCell:
    """A complete preregistered nuisance scenario, never an outcome-tuned setting."""

    cell_id: str
    seed: int
    endpoint_target_effects: tuple[float, ...]
    endpoint_scales: tuple[str, ...]
    endpoint_baselines: tuple[float, ...]
    correlation: tuple[tuple[float, ...], ...]
    assignment_design: str
    estimator: str
    nuisance_source: str
    missingness_rate: float = 0.0
    attrition_rate: float = 0.0
    contamination_rate: float = 0.0
    censoring_rate: float = 0.0
    block_count: int | None = None
    pair_count: int | None = None
    cluster_count: int | None = None
    sequence_count: int | None = None
    pair_correlation: float | None = None
    cluster_icc: float | None = None
    missingness_mechanism: str = "mcar"
    attrition_mechanism: str = "differential_arm"
    contamination_mechanism: str = "cross_arm"
    censoring_policy: str = "administrative"
    target_effect_source: str = "protocol_preregistered"
    invalid_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.cell_id or not isinstance(self.seed, int) or self.seed < 0:
            raise AnalysisError("simulation_cell_identity_invalid")
        if (
            self.assignment_design not in _DESIGN_ESTIMATORS
            or self.estimator != _DESIGN_ESTIMATORS[self.assignment_design]
        ):
            raise AnalysisError("simulation_estimator_design_mismatch")
        if self.nuisance_source not in {"baseline_only", "arm_blind_escrow"}:
            raise AnalysisError("decoded_pilot_efficacy_forbidden")
        if self.target_effect_source != "protocol_preregistered":
            raise AnalysisError("decoded_pilot_efficacy_forbidden")
        if not all(
            isinstance(value, (int, float)) and 0.0 <= value <= 1.0
            for value in (
                self.missingness_rate,
                self.attrition_rate,
                self.contamination_rate,
                self.censoring_rate,
            )
        ):
            raise AnalysisError("simulation_probability_invalid")
        if (
            self.missingness_mechanism != "mcar"
            or self.attrition_mechanism != "differential_arm"
            or self.contamination_mechanism != "cross_arm"
            or self.censoring_policy != "administrative"
        ):
            raise AnalysisError("simulation_mechanism_unsupported")
        effects = tuple(float(value) for value in self.endpoint_target_effects)
        scales = tuple(self.endpoint_scales)
        baselines = tuple(float(value) for value in self.endpoint_baselines)
        if not effects or len(effects) != len(scales) or len(effects) != len(baselines):
            raise AnalysisError("simulation_endpoint_policy_incomplete")
        if (
            any(not math.isfinite(value) for value in effects)
            or any(scale not in {"difference", "ratio"} for scale in scales)
            or any(
                not math.isfinite(baseline)
                or (scale == "difference" and not 0.0 < baseline < 1.0)
                or (scale == "ratio" and baseline <= 0.0)
                for baseline, scale in zip(baselines, scales, strict=True)
            )
            or any(
                scale == "ratio" and value <= 0.0
                for value, scale in zip(effects, scales, strict=True)
            )
        ):
            raise AnalysisError("simulation_endpoint_policy_invalid")
        matrix = tuple(tuple(float(value) for value in row) for row in self.correlation)
        if (
            len(matrix) != len(effects)
            or any(len(row) != len(effects) for row in matrix)
            or any(not math.isfinite(value) or abs(value) > 1.0 for row in matrix for value in row)
            or any(
                matrix[index][index] != 1.0 or matrix[index][other] != matrix[other][index]
                for index in range(len(matrix))
                for other in range(len(matrix))
            )
        ):
            raise AnalysisError("simulation_correlation_invalid")
        counts = {
            "blocked": self.block_count,
            "paired": self.pair_count,
            "clustered": self.cluster_count,
            "sequence": self.sequence_count,
        }
        count = counts[self.assignment_design]
        if (
            count is None
            or any(
                value is not None
                for name, value in counts.items()
                if name != self.assignment_design
            )
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 2
        ):
            raise AnalysisError("simulation_hierarchy_invalid")
        if self.assignment_design == "paired":
            if self.pair_correlation is None or self.cluster_icc is not None:
                raise AnalysisError("simulation_hierarchy_invalid")
        elif self.assignment_design == "clustered":
            if self.cluster_icc is None or self.pair_correlation is not None:
                raise AnalysisError("simulation_hierarchy_invalid")
        elif self.pair_correlation is not None or self.cluster_icc is not None:
            raise AnalysisError("simulation_hierarchy_invalid")
        if self.pair_correlation is not None and not 0.0 <= self.pair_correlation < 1.0:
            raise AnalysisError("simulation_pair_correlation_invalid")
        if self.cluster_icc is not None and not 0.0 <= self.cluster_icc < 1.0:
            raise AnalysisError("simulation_cluster_icc_invalid")
        if _cholesky(matrix) is None:
            if self.invalid_reason != "correlation_not_positive_definite":
                raise AnalysisError("simulation_invalid_reason_unregistered")
        elif self.invalid_reason is not None:
            raise AnalysisError("simulation_invalid_reason_unregistered")
        object.__setattr__(self, "endpoint_target_effects", effects)
        object.__setattr__(self, "endpoint_scales", scales)
        object.__setattr__(self, "endpoint_baselines", baselines)
        object.__setattr__(self, "correlation", matrix)

    @property
    def cell_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "cell_id": self.cell_id,
            "seed": self.seed,
            "endpoint_target_effects": list(self.endpoint_target_effects),
            "endpoint_scales": list(self.endpoint_scales),
            "endpoint_baselines": list(self.endpoint_baselines),
            "correlation": [list(row) for row in self.correlation],
            "assignment_design": self.assignment_design,
            "estimator": self.estimator,
            "nuisance_source": self.nuisance_source,
            "missingness_rate": self.missingness_rate,
            "attrition_rate": self.attrition_rate,
            "contamination_rate": self.contamination_rate,
            "censoring_rate": self.censoring_rate,
            "block_count": self.block_count,
            "pair_count": self.pair_count,
            "cluster_count": self.cluster_count,
            "sequence_count": self.sequence_count,
            "pair_correlation": self.pair_correlation,
            "cluster_icc": self.cluster_icc,
            "missingness_mechanism": self.missingness_mechanism,
            "attrition_mechanism": self.attrition_mechanism,
            "contamination_mechanism": self.contamination_mechanism,
            "censoring_policy": self.censoring_policy,
            "target_effect_source": self.target_effect_source,
            "invalid_reason": self.invalid_reason,
        }


@dataclass(frozen=True, slots=True)
class FrozenPowerProtocol:
    """A sealed, family-complete protocol bound to exact Story 5.3 estimands."""

    protocol_sha256: str
    family: FrozenClaimFamily
    assignment_plan_sha256: str
    estimator_registry: FrozenEstimatorRegistry
    endpoint_estimands: tuple[FrozenEstimand, ...]
    fixed_n: int
    simulation_trials: int
    cells: tuple[FrozenSimulationCell, ...]
    power_endpoint_id: str
    sealed_claim_protocol: SealedClaimProtocol | None = None
    target_power: float = 0.80
    estimator_version: str = POWER_ESTIMATOR_VERSION
    frozen_before_outcomes: bool = True

    def __post_init__(self) -> None:
        if not _valid_sha256(self.protocol_sha256) or not _valid_sha256(
            self.assignment_plan_sha256
        ):
            raise AnalysisError("power_protocol_lineage_invalid")
        if (
            self.family.protocol_sha256 != self.protocol_sha256
            or self.family.assignment_plan_sha256 != self.assignment_plan_sha256
            or self.family.estimand_registry_sha256 != self.estimator_registry.registry_sha256
            or self.fixed_n != PRIMARY_INFORMATION_UNITS
            or self.family.enrolled_n != self.fixed_n
            or self.simulation_trials < MIN_SIMULATION_TRIALS
            or self.target_power != 0.80
            or self.estimator_version != POWER_ESTIMATOR_VERSION
            or not self.frozen_before_outcomes
            or not self.cells
            or self.power_endpoint_id not in self.family.endpoint_ids
        ):
            raise AnalysisError("power_protocol_invalid")
        cells = tuple(sorted(self.cells, key=lambda cell: cell.cell_id))
        if len({cell.cell_id for cell in cells}) != len(cells):
            raise AnalysisError("power_protocol_invalid")
        if len({cell.assignment_design for cell in cells}) != 1:
            raise AnalysisError("power_protocol_assignment_design_drift")
        estimands = tuple(self.endpoint_estimands)
        if len(estimands) != len(self.family.endpoint_ids):
            raise AnalysisError("power_protocol_estimand_map_incomplete")
        for endpoint_id, scale, estimand in zip(
            self.family.endpoint_ids, self.family.endpoint_scales, estimands, strict=True
        ):
            if (
                estimand.endpoint_id != endpoint_id
                or estimand.summary_measure != scale
                or estimand.protocol_sha256 != self.protocol_sha256
                or estimand.assignment_plan_sha256 != self.assignment_plan_sha256
                or estimand.fingerprint
                not in {registered.fingerprint for registered in self.estimator_registry.estimands}
            ):
                raise AnalysisError("power_protocol_estimand_drift")
        expected_design = _DESIGN_RESAMPLING[cells[0].assignment_design]
        if any(item.resampling_design != expected_design for item in estimands):
            raise AnalysisError("power_protocol_assignment_design_drift")
        for cell in cells:
            self._validate_cell(cell)
        object.__setattr__(self, "cells", cells)
        object.__setattr__(self, "endpoint_estimands", estimands)
        if self.sealed_claim_protocol is not None:
            self.validate_against_seal()

    @property
    def family_sha256(self) -> str:
        return self.family.family_sha256

    @property
    def family_registration_sha256(self) -> str:
        return self.family.family_registration_sha256

    @property
    def power_registration_sha256(self) -> str:
        return canonical_digest(self._registration_document())

    @property
    def power_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def _validate_cell(self, cell: FrozenSimulationCell) -> None:
        count = {
            "blocked": cell.block_count,
            "paired": cell.pair_count,
            "clustered": cell.cluster_count,
            "sequence": cell.sequence_count,
        }[cell.assignment_design]
        if (
            len(cell.correlation) != len(self.family.endpoint_ids)
            or len(cell.endpoint_target_effects) != len(self.family.endpoint_ids)
            or cell.endpoint_scales != self.family.endpoint_scales
            or count is None
            or count > self.fixed_n
            or self.fixed_n % count
            or count % 2
        ):
            raise AnalysisError("power_protocol_family_endpoint_drift")
        if cell.assignment_design == "paired" and count != self.fixed_n // 2:
            raise AnalysisError("simulation_hierarchy_invalid")
        if cell.assignment_design != "paired" and self.fixed_n // count < 2:
            raise AnalysisError("simulation_hierarchy_invalid")

    def validate_against_seal(self) -> None:
        seal = self.sealed_claim_protocol
        if seal is None:
            raise AnalysisError("sealed_claim_power_required")
        seal.require_family(
            family_id=self.family.family_id,
            protocol_sha256=self.family.protocol_sha256,
            assignment_plan_sha256=self.family.assignment_plan_sha256,
            estimator_registry_sha256=self.family.estimand_registry_sha256,
            family_registration_sha256=self.family_registration_sha256,
            sealed_claim_protocol_sha256=self.family.sealed_claim_protocol_sha256,
        )
        seal.require_power(
            family_id=self.family.family_id,
            family_registration_sha256=self.family_registration_sha256,
            power_registration_sha256=self.power_registration_sha256,
            sealed_claim_protocol_sha256=seal.sealed_claim_protocol_sha256,
        )

    def _registration_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "frozen_power_protocol_registration",
            "protocol_sha256": self.protocol_sha256,
            "family_id": self.family.family_id,
            "family_registration_sha256": self.family_registration_sha256,
            "assignment_plan_sha256": self.assignment_plan_sha256,
            "estimator_registry_sha256": self.estimator_registry.registry_sha256,
            "endpoint_estimand_fingerprints": [
                item.fingerprint for item in self.endpoint_estimands
            ],
            "fixed_n": self.fixed_n,
            "simulation_trials": self.simulation_trials,
            "power_endpoint_id": self.power_endpoint_id,
            "target_power": self.target_power,
            "estimator_version": self.estimator_version,
            "frozen_before_outcomes": self.frozen_before_outcomes,
            "cells": [cell.to_document() for cell in self.cells],
        }

    def to_document(self) -> dict[str, object]:
        return {
            **self._registration_document(),
            "artifact_type": "frozen_power_protocol",
            "family_sha256": self.family_sha256,
            "family": self.family.to_document(),
            "sealed_claim_protocol_sha256": (
                None
                if self.sealed_claim_protocol is None
                else self.sealed_claim_protocol.sealed_claim_protocol_sha256
            ),
            "power_registration_sha256": self.power_registration_sha256,
        }


@dataclass(frozen=True, slots=True)
class FinalInformationProof:
    protocol_sha256: str
    power_sha256: str
    observed_n: int
    source_sha256: str

    def __post_init__(self) -> None:
        if (
            not all(
                _valid_sha256(value)
                for value in (self.protocol_sha256, self.power_sha256, self.source_sha256)
            )
            or not isinstance(self.observed_n, int)
            or isinstance(self.observed_n, bool)
        ):
            raise AnalysisError("final_information_proof_invalid")

    @property
    def information_sha256(self) -> str:
        return canonical_digest(
            {
                "schema_version": "1.0.0",
                "artifact_type": "final_information_proof",
                "protocol_sha256": self.protocol_sha256,
                "power_sha256": self.power_sha256,
                "observed_n": self.observed_n,
                "source_sha256": self.source_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class SimulationCellResult:
    cell_id: str
    cell_sha256: str
    status: str
    successful_trials: int
    valid_trials: int
    power: float | None
    family_sha256: str
    power_endpoint_id: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.cell_id
            or not _valid_sha256(self.cell_sha256)
            or not _valid_sha256(self.family_sha256)
            or not self.power_endpoint_id
            or self.status not in {"estimated", "invalid"}
            or not isinstance(self.successful_trials, int)
            or not isinstance(self.valid_trials, int)
            or self.successful_trials < 0
            or self.valid_trials < 0
        ):
            raise AnalysisError("simulation_cell_result_invalid")


@dataclass(frozen=True, slots=True)
class PowerEvaluation:
    """Evidence issued only by :func:`evaluate_power` and verified against a protocol."""

    protocol_sha256: str
    power_sha256: str
    family_sha256: str
    sealed_claim_protocol_sha256: str
    status: str
    worst_case_power: float | None
    cells: tuple[SimulationCellResult, ...]
    independent_spot_check_sha256: str
    _issued_for_power_sha256: str | None = field(
        init=False, default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not all(
            _valid_sha256(value)
            for value in (
                self.protocol_sha256,
                self.power_sha256,
                self.family_sha256,
                self.sealed_claim_protocol_sha256,
                self.independent_spot_check_sha256,
            )
        ) or self.status not in {"pass", "estimation_only", "blocked"}:
            raise AnalysisError("power_evaluation_lineage_invalid")
        cells = tuple(self.cells)
        if not cells or len({cell.cell_id for cell in cells}) != len(cells):
            raise AnalysisError("power_evaluation_cell_lineage_invalid")
        object.__setattr__(self, "cells", cells)

    @property
    def evaluation_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def validate_against(self, protocol: FrozenPowerProtocol) -> None:
        """Reject fabricated, partial, unordered, or cross-protocol result records."""

        if not isinstance(protocol, FrozenPowerProtocol):
            raise AnalysisError("claim_power_protocol_required")
        protocol.validate_against_seal()
        if self._issued_for_power_sha256 != protocol.power_sha256:
            raise AnalysisError("power_evaluation_not_evaluator_issued")
        if (
            self.protocol_sha256 != protocol.protocol_sha256
            or self.power_sha256 != protocol.power_sha256
            or self.family_sha256 != protocol.family_sha256
            or self.sealed_claim_protocol_sha256
            != protocol.sealed_claim_protocol.sealed_claim_protocol_sha256
            or len(self.cells) != len(protocol.cells)
        ):
            raise AnalysisError("power_evaluation_protocol_mismatch")
        powers: list[float] = []
        for result, cell in zip(self.cells, protocol.cells, strict=True):
            if (
                result.cell_id != cell.cell_id
                or result.cell_sha256 != cell.cell_sha256
                or result.family_sha256 != protocol.family_sha256
                or result.power_endpoint_id != protocol.power_endpoint_id
            ):
                raise AnalysisError("power_evaluation_cell_mismatch")
            if cell.invalid_reason is not None:
                if (
                    result.status != "invalid"
                    or result.successful_trials != 0
                    or result.valid_trials != 0
                    or result.power is not None
                    or result.reason != cell.invalid_reason
                ):
                    raise AnalysisError("power_evaluation_invalid_cell_mismatch")
                continue
            if (
                result.status != "estimated"
                or result.valid_trials != protocol.simulation_trials
                or not 0 <= result.successful_trials <= result.valid_trials
                or result.reason is not None
                or result.power != result.successful_trials / result.valid_trials
            ):
                raise AnalysisError("power_evaluation_trial_count_invalid")
            powers.append(float(result.power))
        expected_status = "blocked"
        expected_worst: float | None = None
        if len(powers) == len(protocol.cells):
            expected_worst = min(powers)
            expected_status = (
                "pass" if expected_worst >= protocol.target_power else "estimation_only"
            )
        if self.status != expected_status or self.worst_case_power != expected_worst:
            raise AnalysisError("power_evaluation_status_invalid")

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "power_evaluation",
            "protocol_sha256": self.protocol_sha256,
            "power_sha256": self.power_sha256,
            "family_sha256": self.family_sha256,
            "sealed_claim_protocol_sha256": self.sealed_claim_protocol_sha256,
            "status": self.status,
            "worst_case_power": self.worst_case_power,
            "cells": [
                {
                    "cell_id": result.cell_id,
                    "cell_sha256": result.cell_sha256,
                    "status": result.status,
                    "successful_trials": result.successful_trials,
                    "valid_trials": result.valid_trials,
                    "power": result.power,
                    "family_sha256": result.family_sha256,
                    "power_endpoint_id": result.power_endpoint_id,
                    "reason": result.reason,
                }
                for result in self.cells
            ],
            "independent_spot_check_sha256": self.independent_spot_check_sha256,
        }


def evaluate_power(protocol: FrozenPowerProtocol) -> PowerEvaluation:
    """Run every sealed simulation cell through materialized ITT and ``estimate_itt``."""

    protocol.validate_against_seal()
    results = tuple(_simulate_cell(protocol, cell) for cell in protocol.cells)
    powers = [result.power for result in results if result.status == "estimated"]
    status = (
        "blocked"
        if len(powers) != len(results)
        else ("pass" if min(powers) >= protocol.target_power else "estimation_only")
    )
    evaluation = PowerEvaluation(
        protocol.protocol_sha256,
        protocol.power_sha256,
        protocol.family_sha256,
        protocol.sealed_claim_protocol.sealed_claim_protocol_sha256,
        status,
        None if status == "blocked" else min(powers),
        results,
        canonical_digest(
            {
                "protocol": protocol.power_sha256,
                "registered_cells": [
                    {"cell_id": cell.cell_id, "cell_sha256": cell.cell_sha256}
                    for cell in protocol.cells
                ],
            }
        ),
    )
    object.__setattr__(evaluation, "_issued_for_power_sha256", protocol.power_sha256)
    evaluation.validate_against(protocol)
    return evaluation


def fixed_information_look(protocol: FrozenPowerProtocol, *, observed_n: int, purpose: str) -> str:
    if (
        not isinstance(observed_n, int)
        or isinstance(observed_n, bool)
        or not 0 <= observed_n <= protocol.fixed_n
    ):
        raise AnalysisError("fixed_information_count_invalid")
    if purpose not in {"efficacy", "safety", "budget"}:
        raise AnalysisError("fixed_information_purpose_invalid")
    if purpose == "efficacy" and observed_n < protocol.fixed_n:
        raise AnalysisError("efficacy_before_final_information_forbidden")
    return "efficacy_allowed" if purpose == "efficacy" else "monitoring_only"


@dataclass(frozen=True, slots=True)
class _AssignmentLayout:
    hierarchy_ids: tuple[int, ...]
    treatments: tuple[bool, ...]


def _simulate_cell(
    protocol: FrozenPowerProtocol, cell: FrozenSimulationCell
) -> SimulationCellResult:
    if cell.invalid_reason is not None:
        return SimulationCellResult(
            cell.cell_id,
            cell.cell_sha256,
            "invalid",
            0,
            0,
            None,
            protocol.family_sha256,
            protocol.power_endpoint_id,
            cell.invalid_reason,
        )
    successful, valid = _run_trials(
        protocol,
        cell,
        _cholesky(cell.correlation),
        random.Random(cell.seed),
        protocol.simulation_trials,
    )
    return SimulationCellResult(
        cell.cell_id,
        cell.cell_sha256,
        "estimated",
        successful,
        valid,
        successful / valid,
        protocol.family_sha256,
        protocol.power_endpoint_id,
    )


def _run_trials(
    protocol: FrozenPowerProtocol,
    cell: FrozenSimulationCell,
    lower: tuple[tuple[float, ...], ...] | None,
    rng: random.Random,
    trials: int,
) -> tuple[int, int]:
    if lower is None:
        raise AnalysisError("simulation_invalid_reason_unregistered")
    successes = 0
    adapter: _SimulationEstimatorAdapter | None = None
    for trial_index in range(trials):
        layout = _generate_assignment_layout(protocol, cell, rng)
        values, missing, attrited, contaminated = _generate_assignment_outcomes(
            protocol, cell, layout, lower, rng
        )
        if adapter is None:
            tables = tuple(
                _materialize_itt_table(
                    protocol,
                    cell,
                    estimand,
                    endpoint_index,
                    trial_index,
                    layout,
                    values,
                    missing,
                    attrited,
                    contaminated,
                )
                for endpoint_index, estimand in enumerate(protocol.endpoint_estimands)
            )
            # The adapter is initialized only after every endpoint completes the exact
            # Story 5.3 path; subsequent trials reuse its identical randomization
            # statistic over compact assignment-unit ITT arrays.
            tuple(
                estimate_itt(table, permutation_count=_SIMULATION_PERMUTATIONS) for table in tables
            )
            adapter = _SimulationEstimatorAdapter(cell, layout)
        estimates = adapter.estimate(protocol, layout, values, missing, attrited)
        p_values = _trial_p_values(protocol.family, estimates)
        if p_values is not None:
            target = next(
                result
                for result in holm_fwer(protocol.family, p_values)
                if result.endpoint_id == protocol.power_endpoint_id
            )
            successes += int(target.rejection)
    return successes, trials


@dataclass(frozen=True, slots=True)
class _SimulationEstimatorAdapter:
    """Exact compact adapter for the already-qualified Story 5.3 ITT statistic."""

    cell: FrozenSimulationCell
    selector: np.ndarray

    def __init__(self, cell: FrozenSimulationCell, layout: _AssignmentLayout) -> None:
        groups: dict[int, list[int]] = {}
        for index, group in enumerate(layout.hierarchy_ids):
            groups.setdefault(group, []).append(index)
        values = list(groups.values())
        rng = random.Random(0)
        selector = np.zeros((_SIMULATION_PERMUTATIONS, len(layout.treatments)), dtype=float)
        if cell.assignment_design == "clustered":
            selected_count = len(values) // 2
            for row in range(_SIMULATION_PERMUTATIONS):
                for group in rng.sample(values, selected_count):
                    selector[row, group] = 1.0
        else:
            counts = [sum(layout.treatments[index] for index in group) for group in values]
            for row in range(_SIMULATION_PERMUTATIONS):
                for group, count in zip(values, counts, strict=True):
                    selector[row, rng.sample(group, count)] = 1.0
        object.__setattr__(self, "cell", cell)
        object.__setattr__(self, "selector", selector)

    def estimate(
        self,
        protocol: FrozenPowerProtocol,
        layout: _AssignmentLayout,
        values: tuple[tuple[float, ...], ...],
        missing: tuple[set[int], ...],
        attrited: set[int],
    ) -> tuple[EffectEstimate, ...]:
        treatment = np.asarray(layout.treatments, dtype=bool)
        result: list[EffectEstimate] = []
        for endpoint, estimand in enumerate(protocol.endpoint_estimands):
            if attrited or missing[endpoint]:
                result.append(
                    EffectEstimate(
                        None,
                        None,
                        protocol.fixed_n,
                        "indeterminate",
                        "primary_outcome_missing_requires_frozen_bounds",
                    )
                )
                continue
            endpoint_values = np.asarray([value[endpoint] for value in values], dtype=float)
            treatment_mean = float(endpoint_values[treatment].mean())
            control_mean = float(endpoint_values[~treatment].mean())
            point = (
                treatment_mean - control_mean
                if estimand.summary_measure == "difference"
                else treatment_mean / control_mean
            )
            selected = self.selector @ endpoint_values
            selected_n = int(self.selector[0].sum())
            randomized_treatment = selected / selected_n
            randomized_control = (endpoint_values.sum() - selected) / (
                protocol.fixed_n - selected_n
            )
            statistics = (
                randomized_treatment - randomized_control
                if estimand.summary_measure == "difference"
                else np.log(randomized_treatment / randomized_control)
            )
            observed = point if estimand.summary_measure == "difference" else math.log(point)
            p_value = (int(np.count_nonzero(np.abs(statistics) >= abs(observed))) + 1) / (
                _SIMULATION_PERMUTATIONS + 1
            )
            result.append(EffectEstimate(point, p_value, protocol.fixed_n, "estimated"))
        return tuple(result)


def _generate_assignment_layout(
    protocol: FrozenPowerProtocol, cell: FrozenSimulationCell, rng: random.Random
) -> _AssignmentLayout:
    count = _hierarchy_count(cell)
    group_size = protocol.fixed_n // count
    hierarchy: list[int] = []
    treatments: list[bool] = []
    cluster_arms = [True] * (count // 2) + [False] * (count // 2)
    if cell.assignment_design == "clustered":
        rng.shuffle(cluster_arms)
    for group in range(count):
        if cell.assignment_design == "clustered":
            flags = [cluster_arms[group]] * group_size
        elif cell.assignment_design == "paired":
            flags = [bool(rng.getrandbits(1)), False]
            flags[1] = not flags[0]
        elif cell.assignment_design == "sequence":
            start = rng.randrange(2)
            flags = [bool((position + start) % 2) for position in range(group_size)]
        else:
            flags = [True] * (group_size // 2) + [False] * (group_size // 2)
            rng.shuffle(flags)
        hierarchy.extend([group] * group_size)
        treatments.extend(flags)
    return _AssignmentLayout(tuple(hierarchy), tuple(treatments))


def _generate_assignment_outcomes(
    protocol: FrozenPowerProtocol,
    cell: FrozenSimulationCell,
    layout: _AssignmentLayout,
    lower: tuple[tuple[float, ...], ...],
    rng: random.Random,
) -> tuple[tuple[tuple[float, ...], ...], tuple[set[int], ...], set[int], set[int]]:
    endpoint_count = len(protocol.family.endpoint_ids)
    attrited = _attrited_unit_ids(layout, cell.attrition_rate, rng)
    treated = [
        index for index, value in enumerate(layout.treatments) if value and index not in attrited
    ]
    contaminated = _sample_ids(treated, cell.contamination_rate, rng)
    censored = _sample_ids(treated, cell.censoring_rate, rng)
    available = [index for index in range(protocol.fixed_n) if index not in attrited]
    missing = tuple(
        _sample_ids(available, cell.missingness_rate, rng) for _ in range(endpoint_count)
    )
    group_noise = {group: _correlated_normal(lower, rng) for group in set(layout.hierarchy_ids)}
    values: list[tuple[float, ...]] = []
    for unit_id, treatment in enumerate(layout.treatments):
        residual = _correlated_normal(lower, rng)
        effect_multiplier = 0.0 if unit_id in contaminated else 1.0
        if unit_id in censored:
            effect_multiplier *= 0.5
        values.append(
            tuple(
                _simulated_value(
                    cell.endpoint_baselines[index],
                    cell.endpoint_target_effects[index],
                    scale,
                    0.35 * group_noise[layout.hierarchy_ids[unit_id]][index]
                    + 0.65 * residual[index],
                    treatment,
                    effect_multiplier,
                )
                for index, scale in enumerate(cell.endpoint_scales)
            )
        )
    return tuple(values), missing, attrited, contaminated


def _materialize_itt_table(
    protocol: FrozenPowerProtocol,
    cell: FrozenSimulationCell,
    estimand: FrozenEstimand,
    endpoint_index: int,
    trial_index: int,
    layout: _AssignmentLayout,
    values: tuple[tuple[float, ...], ...],
    missing: tuple[set[int], ...],
    attrited: set[int],
    contaminated: set[int],
) -> IttTable:
    observations: list[IttObservation] = []
    for unit_id, treatment in enumerate(layout.treatments):
        assignment_id = f"simulation-{trial_index}-{unit_id}"
        hierarchy_id = layout.hierarchy_ids[unit_id]
        absent = unit_id in attrited or unit_id in missing[endpoint_index]
        sequence_id = (
            assignment_id if estimand.history_mode == "dynamic" else f"sequence-{hierarchy_id}"
        )
        disclosure = AssignmentDisclosure(
            assignment_id=assignment_id,
            arm=estimand.treatment_arm if treatment else estimand.control_arm,
            analysis_unit_id=assignment_id,
            block_id=(
                "simulation-cluster-randomization"
                if cell.assignment_design == "clustered"
                else f"simulation-block-{hierarchy_id}"
            ),
            cluster_id=f"simulation-cluster-{hierarchy_id}",
            resampling_unit_id=sequence_id
            if estimand.history_mode == "dynamic"
            else f"unit-{unit_id}",
            model_role=protocol.family.model_role,
            assignment_plan_sha256=protocol.assignment_plan_sha256,
            pair_id=(f"pair-{hierarchy_id}" if cell.assignment_design == "paired" else None),
            pairing_is_registered_and_fresh=cell.assignment_design == "paired",
        )
        observations.append(
            IttObservation(
                assignment_id=assignment_id,
                analysis_unit_id=assignment_id,
                arm=disclosure.arm,
                task_id="simulated-primary",
                sequence_id=sequence_id,
                repository_id="simulation",
                environment_fingerprint_sha256=protocol.family.environment_fingerprint_sha256,
                terminal_kind="succeeded" if not absent else "evidence_incomplete",
                inclusion_status="included",
                attrition_status="attrited" if unit_id in attrited else "complete",
                exposure_status="exposed" if unit_id in contaminated else "unexposed",
                contamination_status="contaminated" if unit_id in contaminated else "isolated",
                outcome_status="missing" if absent else "eligible",
                numeric_value=None if absent else values[unit_id][endpoint_index],
                outcome_id=None if absent else f"simulated-{estimand.endpoint_id}",
                disclosure=disclosure,
            )
        )
    return IttTable(
        estimand=estimand,
        registry_sha256=protocol.estimator_registry.registry_sha256,
        source_dataset_manifest_sha256=estimand.source_dataset_manifest_sha256,
        assignment_analysis_lock_sha256=canonical_digest(
            {"cell": cell.cell_sha256, "trial": trial_index, "endpoint": estimand.endpoint_id}
        ),
        observations=tuple(observations),
    )


def _trial_p_values(
    family: FrozenClaimFamily, estimates: tuple[EffectEstimate, ...]
) -> tuple[EndpointPValue, ...] | None:
    if any(item.status != "estimated" or item.p_value is None for item in estimates):
        return None
    return tuple(
        EndpointPValue(endpoint_id, estimate.p_value)
        for endpoint_id, estimate in zip(family.endpoint_ids, estimates, strict=True)
    )


def _hierarchy_count(cell: FrozenSimulationCell) -> int:
    count = {
        "blocked": cell.block_count,
        "paired": cell.pair_count,
        "clustered": cell.cluster_count,
        "sequence": cell.sequence_count,
    }[cell.assignment_design]
    assert count is not None
    return count


def _sample_ids(ids: list[int], rate: float, rng: random.Random) -> set[int]:
    return set() if rate == 0.0 else set(rng.sample(ids, round(rate * len(ids))))


def _attrited_unit_ids(layout: _AssignmentLayout, rate: float, rng: random.Random) -> set[int]:
    treated = [index for index, value in enumerate(layout.treatments) if value]
    controls = [index for index, value in enumerate(layout.treatments) if not value]
    return _sample_ids(treated, rate, rng) | _sample_ids(controls, rate / 2.0, rng)


def _correlated_normal(
    lower: tuple[tuple[float, ...], ...], rng: random.Random
) -> tuple[float, ...]:
    independent = [rng.gauss(0.0, 1.0) for _ in lower]
    return tuple(
        sum(lower[index][inner] * independent[inner] for inner in range(index + 1))
        for index in range(len(lower))
    )


def _simulated_value(
    baseline: float,
    effect: float,
    scale: str,
    noise: float,
    treatment: bool,
    effect_multiplier: float,
) -> float:
    if scale == "difference":
        return min(
            0.99999,
            max(
                0.00001,
                baseline + (effect * effect_multiplier if treatment else 0.0) + 0.22 * noise,
            ),
        )
    return math.exp(
        math.log(baseline)
        + (math.log(effect) * effect_multiplier if treatment else 0.0)
        + 0.22 * noise
    )


def _cholesky(matrix: tuple[tuple[float, ...], ...]) -> tuple[tuple[float, ...], ...] | None:
    lower = [[0.0 for _ in row] for row in matrix]
    for row in range(len(matrix)):
        for column in range(row + 1):
            value = matrix[row][column] - sum(
                lower[row][inner] * lower[column][inner] for inner in range(column)
            )
            if row == column:
                if value <= 1e-12:
                    return None
                lower[row][column] = math.sqrt(value)
            else:
                lower[row][column] = value / lower[column][column]
    return tuple(tuple(row) for row in lower)
