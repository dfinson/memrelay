"""Frozen, outcome-independent estimand and estimator registration."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import AnalysisError

ANALYSIS_SCHEMA_VERSION = "1.0.0"
MULTIPLICITY_OWNER = "story_5_4"
_HISTORY_ESTIMANDS = {
    "controlled": "controlled_access_effect",
    "dynamic": "total_policy_sequence_effect",
}
_SUMMARY_MEASURES = frozenset({"difference", "ratio"})
_RESAMPLING_DESIGNS = frozenset(
    {"blocked_randomization", "paired_sign_flip", "sequence_randomization"}
)
_ASSIGNMENT_MECHANISMS = {
    "blocked_randomization": "blocked_randomization",
    "paired_sign_flip": "paired_randomization",
    "sequence_randomization": "sequence_randomization",
}


@dataclass(frozen=True, slots=True)
class MissingnessPolicy:
    """Registered handling for missing primary evidence; it never deletes an assignment."""

    lower_bound: float
    upper_bound: float
    pattern_mixture_by_arm: Mapping[str, float]

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.lower_bound)
            or not math.isfinite(self.upper_bound)
            or self.lower_bound > self.upper_bound
        ):
            raise AnalysisError("missingness_bounds_invalid")
        values = dict(self.pattern_mixture_by_arm)
        if not values or any(
            not isinstance(arm, str)
            or not arm
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
            for arm, value in values.items()
        ):
            raise AnalysisError("pattern_mixture_policy_invalid")
        object.__setattr__(self, "pattern_mixture_by_arm", MappingProxyType(values))

    def to_document(self) -> dict[str, object]:
        return {
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "pattern_mixture_by_arm": dict(sorted(self.pattern_mixture_by_arm.items())),
        }


@dataclass(frozen=True, slots=True)
class FrozenEstimand:
    """The complete, pre-outcome causal target and its allowed estimator."""

    estimand_id: str
    version: str
    protocol_sha256: str
    source_dataset_manifest_sha256: str
    assignment_plan_sha256: str
    endpoint_id: str
    population_id: str
    stratum: str
    history_mode: str
    assignment_mechanism: str
    treatment_strategy: str
    intercurrent_event_policy: str
    summary_measure: str
    treatment_arm: str
    control_arm: str
    assignment_unit: str
    experimental_unit: str
    observation_unit: str
    resampling_unit: str
    clustering_unit: str
    analysis_unit: str
    resampling_design: str
    missingness_policy: MissingnessPolicy
    estimator_version: str = "1.0.0"
    multiplicity_owner: str = MULTIPLICITY_OWNER

    def __post_init__(self) -> None:
        required = (
            self.estimand_id,
            self.version,
            self.protocol_sha256,
            self.source_dataset_manifest_sha256,
            self.assignment_plan_sha256,
            self.endpoint_id,
            self.population_id,
            self.stratum,
            self.history_mode,
            self.assignment_mechanism,
            self.treatment_strategy,
            self.intercurrent_event_policy,
            self.treatment_arm,
            self.control_arm,
            self.assignment_unit,
            self.experimental_unit,
            self.observation_unit,
            self.resampling_unit,
            self.clustering_unit,
            self.analysis_unit,
            self.estimator_version,
        )
        if any(not isinstance(value, str) or not value for value in required):
            raise AnalysisError("frozen_estimand_identity_missing")
        if (
            not _is_sha256(self.protocol_sha256)
            or not _is_sha256(self.source_dataset_manifest_sha256)
            or not _is_sha256(self.assignment_plan_sha256)
        ):
            raise AnalysisError("frozen_estimand_lineage_invalid")
        if self.treatment_arm == self.control_arm:
            raise AnalysisError("estimand_contrast_invalid")
        if self.summary_measure not in _SUMMARY_MEASURES:
            raise AnalysisError("summary_measure_unsupported")
        if self.resampling_design not in _RESAMPLING_DESIGNS:
            raise AnalysisError("resampling_design_unsupported")
        if _ASSIGNMENT_MECHANISMS[self.resampling_design] != self.assignment_mechanism:
            raise AnalysisError("assignment_resampling_design_mismatch")
        expected_estimand = _HISTORY_ESTIMANDS.get(self.history_mode)
        if expected_estimand is None:
            raise AnalysisError("history_mode_unsupported")
        if self.treatment_strategy != expected_estimand:
            raise AnalysisError("history_estimand_mismatch")
        if self.multiplicity_owner != MULTIPLICITY_OWNER:
            raise AnalysisError("multiplicity_leakage")
        if set(self.missingness_policy.pattern_mixture_by_arm) != {
            self.treatment_arm,
            self.control_arm,
        }:
            raise AnalysisError("pattern_mixture_arms_invalid")
        if self.history_mode == "dynamic" and (
            self.assignment_unit != "sequence"
            or self.experimental_unit != "sequence"
            or self.resampling_unit != "sequence"
            or self.observation_unit != "sequence"
            or self.clustering_unit != "sequence"
            or self.analysis_unit != "sequence"
        ):
            raise AnalysisError("dynamic_sequence_unit_mismatch")
        if self.history_mode == "controlled" and self.resampling_design == "sequence_randomization":
            raise AnalysisError("controlled_sequence_resampling_forbidden")

    @property
    def fingerprint(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "estimand_id": self.estimand_id,
            "version": self.version,
            "protocol_sha256": self.protocol_sha256,
            "source_dataset_manifest_sha256": self.source_dataset_manifest_sha256,
            "assignment_plan_sha256": self.assignment_plan_sha256,
            "endpoint_id": self.endpoint_id,
            "population_id": self.population_id,
            "stratum": self.stratum,
            "history_mode": self.history_mode,
            "assignment_mechanism": self.assignment_mechanism,
            "treatment_strategy": self.treatment_strategy,
            "intercurrent_event_policy": self.intercurrent_event_policy,
            "summary_measure": self.summary_measure,
            "treatment_arm": self.treatment_arm,
            "control_arm": self.control_arm,
            "assignment_unit": self.assignment_unit,
            "experimental_unit": self.experimental_unit,
            "observation_unit": self.observation_unit,
            "resampling_unit": self.resampling_unit,
            "clustering_unit": self.clustering_unit,
            "analysis_unit": self.analysis_unit,
            "resampling_design": self.resampling_design,
            "missingness_policy": self.missingness_policy.to_document(),
            "estimator_version": self.estimator_version,
            "multiplicity_owner": self.multiplicity_owner,
        }


class FrozenEstimatorRegistry:
    """An immutable registry with exact lookup and no data-dependent fallback."""

    def __init__(self, estimands: Sequence[FrozenEstimand]) -> None:
        ordered = tuple(sorted(estimands, key=lambda item: (item.estimand_id, item.version)))
        if not ordered:
            raise AnalysisError("estimator_registry_empty")
        keys = [(item.estimand_id, item.version) for item in ordered]
        if len(set(keys)) != len(keys):
            raise AnalysisError("estimator_registry_duplicate")
        self._estimands = ordered
        self._by_key = dict(zip(keys, ordered, strict=True))
        self._digest = canonical_digest(self.to_document())

    @property
    def registry_sha256(self) -> str:
        return self._digest

    @property
    def estimands(self) -> tuple[FrozenEstimand, ...]:
        return self._estimands

    def require(
        self,
        estimand_id: str,
        version: str,
        *,
        protocol_sha256: str,
        source_dataset_manifest_sha256: str,
    ) -> FrozenEstimand:
        estimand = self._by_key.get((estimand_id, version))
        if estimand is None:
            raise AnalysisError("estimator_not_registered")
        if estimand.protocol_sha256 != protocol_sha256:
            raise AnalysisError("estimator_protocol_drift")
        if estimand.source_dataset_manifest_sha256 != source_dataset_manifest_sha256:
            raise AnalysisError("estimator_source_dataset_drift")
        return estimand

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "registry": [item.to_document() for item in self._estimands],
            "multiplicity_owner": MULTIPLICITY_OWNER,
        }


def _is_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and value.isascii()
        and all(character in "0123456789abcdef" for character in value)
    )
