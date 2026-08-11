"""Frozen safety-denominator and detector-sensitivity analysis."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Final

from memrelay_eval.analysis.schemas import SAFETY_SCHEMA_VERSION
from memrelay_eval.canonical import attach_digest, canonical_digest
from memrelay_eval.domain.errors import SafetyAnalysisError

_SHA256_LENGTH: Final = 64
_ONE_SIDED_CONFIDENCE: Final = 0.95


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    """A preregistered detector policy whose hash binds every safety result."""

    policy_id: str
    detector_id: str
    detector_version: str
    injected_positive_plan_sha256: str
    sensitivity_model_sha256: str
    threshold_sha256: str
    confidence: float = _ONE_SIDED_CONFIDENCE
    schema_version: str = SAFETY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.policy_id or not self.detector_id or not self.detector_version:
            raise SafetyAnalysisError("safety_policy_identity_missing")
        if self.schema_version != SAFETY_SCHEMA_VERSION:
            raise SafetyAnalysisError("unsupported_safety_schema_version")
        if self.confidence != _ONE_SIDED_CONFIDENCE:
            raise SafetyAnalysisError("safety_confidence_not_frozen")
        for value in (
            self.injected_positive_plan_sha256,
            self.sensitivity_model_sha256,
            self.threshold_sha256,
        ):
            _require_sha256(value, "safety_policy_hash_invalid")

    @property
    def sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "injected_positive_plan_sha256": self.injected_positive_plan_sha256,
            "sensitivity_model_sha256": self.sensitivity_model_sha256,
            "threshold_sha256": self.threshold_sha256,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class SafetyOpportunity:
    """One independent eligible exposure, counted once before detector results."""

    opportunity_id: str
    gate_id: str
    assignment_id: str
    stratum: str
    history_mode: str
    inclusion_status: str
    evidence_sha256: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all(
            (
                self.opportunity_id,
                self.gate_id,
                self.assignment_id,
                self.stratum,
                self.history_mode,
            )
        ):
            raise SafetyAnalysisError("safety_opportunity_identity_missing")
        if self.inclusion_status not in {"included", "excluded"}:
            raise SafetyAnalysisError("safety_opportunity_inclusion_invalid")
        _require_evidence(self.evidence_sha256, "safety_opportunity_evidence_missing")

    def to_document(self) -> dict[str, object]:
        return {
            "opportunity_id": self.opportunity_id,
            "gate_id": self.gate_id,
            "assignment_id": self.assignment_id,
            "stratum": self.stratum,
            "history_mode": self.history_mode,
            "inclusion_status": self.inclusion_status,
            "evidence_sha256": sorted(self.evidence_sha256),
        }


@dataclass(frozen=True, slots=True)
class DetectorInspection:
    """An immutable detector result; uninspected opportunities have no record."""

    opportunity_id: str
    detector_id: str
    detector_version: str
    result: str
    evidence_sha256: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.opportunity_id or not self.detector_id or not self.detector_version:
            raise SafetyAnalysisError("detector_inspection_identity_missing")
        if self.result not in {"no_event", "event"}:
            raise SafetyAnalysisError("detector_inspection_result_invalid")
        _require_evidence(self.evidence_sha256, "detector_inspection_evidence_missing")

    def to_document(self) -> dict[str, object]:
        return {
            "opportunity_id": self.opportunity_id,
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "result": self.result,
            "evidence_sha256": sorted(self.evidence_sha256),
        }


@dataclass(frozen=True, slots=True)
class InjectedPositive:
    """A preregistered injected-positive result excluded from efficacy denominators."""

    injection_id: str
    detector_id: str
    detector_version: str
    caught: bool
    plan_sha256: str
    evidence_sha256: tuple[str, ...]
    excluded_from_efficacy: bool = True

    def __post_init__(self) -> None:
        if not self.injection_id or not self.detector_id or not self.detector_version:
            raise SafetyAnalysisError("injected_positive_identity_missing")
        if not self.excluded_from_efficacy:
            raise SafetyAnalysisError("injected_positive_in_efficacy_denominator")
        _require_sha256(self.plan_sha256, "injected_positive_plan_hash_invalid")
        _require_evidence(self.evidence_sha256, "injected_positive_evidence_missing")

    def to_document(self) -> dict[str, object]:
        return {
            "injection_id": self.injection_id,
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "caught": self.caught,
            "plan_sha256": self.plan_sha256,
            "evidence_sha256": sorted(self.evidence_sha256),
            "excluded_from_efficacy": self.excluded_from_efficacy,
        }


@dataclass(frozen=True, slots=True)
class HarmIncident:
    """A detected harm preserves severity and attribution uncertainty."""

    incident_id: str
    opportunity_id: str
    severity: str
    attribution: str
    evidence_sha256: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.incident_id or not self.opportunity_id:
            raise SafetyAnalysisError("harm_incident_identity_missing")
        if self.severity not in {"low", "moderate", "high", "critical"}:
            raise SafetyAnalysisError("harm_incident_severity_invalid")
        if self.attribution not in {"confirmed", "probable", "possible", "unknown"}:
            raise SafetyAnalysisError("harm_incident_attribution_invalid")
        _require_evidence(self.evidence_sha256, "harm_incident_evidence_missing")

    def to_document(self) -> dict[str, object]:
        return {
            "incident_id": self.incident_id,
            "opportunity_id": self.opportunity_id,
            "severity": self.severity,
            "attribution": self.attribution,
            "evidence_sha256": sorted(self.evidence_sha256),
        }


@dataclass(frozen=True, slots=True)
class SensitivityScenario:
    """A frozen missingness or attrition sensitivity result."""

    scenario_id: str
    model_sha256: str
    threshold_crossed: bool
    evidence_sha256: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise SafetyAnalysisError("sensitivity_scenario_identity_missing")
        _require_sha256(self.model_sha256, "sensitivity_model_hash_invalid")
        _require_evidence(self.evidence_sha256, "sensitivity_evidence_missing")

    def to_document(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "model_sha256": self.model_sha256,
            "threshold_crossed": self.threshold_crossed,
            "evidence_sha256": sorted(self.evidence_sha256),
        }


@dataclass(frozen=True, slots=True)
class SafetyStratumResult:
    """Bounded safety result for one gate/stratum/history-mode denominator."""

    gate_id: str
    stratum: str
    history_mode: str
    assigned_denominator: int
    included_denominator: int
    inspected_denominator: int
    detected_events: int
    coverage_lower_bound: float
    sensitivity_lower_bound: float | None
    exact_detected_event_upper_bound: float
    adjusted_harm_upper_bound: float | None
    rule_of_three_approximation: float | None
    detector_gate_status: str
    evidence_status: str
    severity_counts: tuple[tuple[str, int], ...]
    attribution_counts: tuple[tuple[str, int], ...]

    def to_document(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "stratum": self.stratum,
            "history_mode": self.history_mode,
            "assigned_denominator": self.assigned_denominator,
            "included_denominator": self.included_denominator,
            "inspected_denominator": self.inspected_denominator,
            "detected_events": self.detected_events,
            "coverage_lower_bound": self.coverage_lower_bound,
            "sensitivity_lower_bound": self.sensitivity_lower_bound,
            "exact_detected_event_upper_bound": self.exact_detected_event_upper_bound,
            "adjusted_harm_upper_bound": self.adjusted_harm_upper_bound,
            "rule_of_three_approximation": self.rule_of_three_approximation,
            "detector_gate_status": self.detector_gate_status,
            "evidence_status": self.evidence_status,
            "severity_counts": dict(self.severity_counts),
            "attribution_counts": dict(self.attribution_counts),
        }


@dataclass(frozen=True, slots=True)
class SafetyEvaluation:
    """Immutable stratified safety evidence suitable only for bounded reporting."""

    policy_sha256: str
    source_manifest_sha256: tuple[str, ...]
    source_evidence_sha256: tuple[str, ...]
    derivation_sha256: str
    results: tuple[SafetyStratumResult, ...]
    sensitivity_scenarios: tuple[SensitivityScenario, ...]
    schema_version: str = SAFETY_SCHEMA_VERSION

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        document = {
            "schema_version": self.schema_version,
            "policy_sha256": self.policy_sha256,
            "source_manifest_sha256": list(self.source_manifest_sha256),
            "source_evidence_sha256": list(self.source_evidence_sha256),
            "derivation_sha256": self.derivation_sha256,
            "results": [item.to_document() for item in self.results],
            "sensitivity_scenarios": [item.to_document() for item in self.sensitivity_scenarios],
        }
        return attach_digest(document)


def evaluate_safety(
    *,
    policy: SafetyPolicy,
    opportunities: tuple[SafetyOpportunity, ...],
    inspections: tuple[DetectorInspection, ...],
    injected_positives: tuple[InjectedPositive, ...],
    incidents: tuple[HarmIncident, ...] = (),
    sensitivity_scenarios: tuple[SensitivityScenario, ...] = (),
    source_manifest_sha256: tuple[str, ...] = (),
) -> SafetyEvaluation:
    """Analyze frozen opportunities without converting missing inspection into no harm."""

    if not opportunities:
        raise SafetyAnalysisError("safety_opportunities_missing")
    _require_evidence(source_manifest_sha256, "safety_source_manifest_missing")
    _require_unique((item.opportunity_id for item in opportunities), "duplicate_safety_opportunity")
    _require_unique((item.opportunity_id for item in inspections), "duplicate_detector_inspection")
    _require_unique(
        (item.injection_id for item in injected_positives), "duplicate_injected_positive"
    )
    _require_unique((item.incident_id for item in incidents), "duplicate_harm_incident")
    _require_unique(
        (item.scenario_id for item in sensitivity_scenarios), "duplicate_sensitivity_scenario"
    )
    if any(item.model_sha256 != policy.sensitivity_model_sha256 for item in sensitivity_scenarios):
        raise SafetyAnalysisError("sensitivity_model_authority_conflict")

    opportunity_by_id = {item.opportunity_id: item for item in opportunities}
    for inspection in inspections:
        if inspection.opportunity_id not in opportunity_by_id:
            raise SafetyAnalysisError("detector_inspection_opportunity_unknown")
        if (inspection.detector_id, inspection.detector_version) != (
            policy.detector_id,
            policy.detector_version,
        ):
            raise SafetyAnalysisError("detector_authority_conflict")
    for injection in injected_positives:
        if (injection.detector_id, injection.detector_version) != (
            policy.detector_id,
            policy.detector_version,
        ) or injection.plan_sha256 != policy.injected_positive_plan_sha256:
            raise SafetyAnalysisError("injected_positive_authority_conflict")
    for incident in incidents:
        if incident.opportunity_id not in opportunity_by_id:
            raise SafetyAnalysisError("harm_incident_opportunity_unknown")

    inspection_by_id = {item.opportunity_id: item for item in inspections}
    incidents_by_opportunity: dict[str, list[HarmIncident]] = defaultdict(list)
    for incident in incidents:
        inspection = inspection_by_id.get(incident.opportunity_id)
        if inspection is None or inspection.result != "event":
            raise SafetyAnalysisError("harm_incident_detector_authority_conflict")
        incidents_by_opportunity[incident.opportunity_id].append(incident)
    for inspection in inspections:
        if inspection.result == "event" and not incidents_by_opportunity[inspection.opportunity_id]:
            raise SafetyAnalysisError("detected_harm_tail_missing")

    grouped: dict[tuple[str, str, str], list[SafetyOpportunity]] = defaultdict(list)
    for opportunity in opportunities:
        grouped[(opportunity.gate_id, opportunity.stratum, opportunity.history_mode)].append(
            opportunity
        )

    results = tuple(
        _evaluate_stratum(
            key,
            members,
            inspection_by_id,
            incidents_by_opportunity,
            injected_positives,
            sensitivity_scenarios,
        )
        for key, members in sorted(grouped.items())
    )
    source_evidence_sha256 = tuple(
        sorted(
            {
                sha
                for values in (
                    *(item.evidence_sha256 for item in opportunities),
                    *(item.evidence_sha256 for item in inspections),
                    *(item.evidence_sha256 for item in injected_positives),
                    *(item.evidence_sha256 for item in incidents),
                    *(item.evidence_sha256 for item in sensitivity_scenarios),
                )
                for sha in values
            }
        )
    )
    derivation_sha256 = canonical_digest(
        {
            "policy": policy.to_document(),
            "source_manifest_sha256": sorted(source_manifest_sha256),
            "opportunities": [
                item.to_document()
                for item in sorted(opportunities, key=lambda item: item.opportunity_id)
            ],
            "inspections": [
                item.to_document()
                for item in sorted(inspections, key=lambda item: item.opportunity_id)
            ],
            "injected_positives": [
                item.to_document()
                for item in sorted(injected_positives, key=lambda item: item.injection_id)
            ],
            "incidents": [
                item.to_document() for item in sorted(incidents, key=lambda item: item.incident_id)
            ],
            "sensitivity_scenarios": [
                item.to_document()
                for item in sorted(sensitivity_scenarios, key=lambda item: item.scenario_id)
            ],
        }
    )
    return SafetyEvaluation(
        policy_sha256=policy.sha256,
        source_manifest_sha256=tuple(sorted(source_manifest_sha256)),
        source_evidence_sha256=source_evidence_sha256,
        derivation_sha256=derivation_sha256,
        results=results,
        sensitivity_scenarios=tuple(
            sorted(sensitivity_scenarios, key=lambda item: item.scenario_id)
        ),
    )


def _evaluate_stratum(
    key: tuple[str, str, str],
    opportunities: list[SafetyOpportunity],
    inspection_by_id: dict[str, DetectorInspection],
    incidents_by_opportunity: dict[str, list[HarmIncident]],
    injected_positives: tuple[InjectedPositive, ...],
    sensitivity_scenarios: tuple[SensitivityScenario, ...],
) -> SafetyStratumResult:
    gate_id, stratum, history_mode = key
    assigned = len(opportunities)
    inspected = [
        inspection_by_id[item.opportunity_id]
        for item in opportunities
        if item.opportunity_id in inspection_by_id
    ]
    detected_events = sum(item.result == "event" for item in inspected)
    coverage_lower = one_sided_clopper_pearson_lower(len(inspected), assigned)
    sensitivity_lower = (
        one_sided_clopper_pearson_lower(
            sum(item.caught for item in injected_positives), len(injected_positives)
        )
        if injected_positives
        else None
    )
    exact_upper = (
        one_sided_clopper_pearson_upper(detected_events, len(inspected)) if inspected else 1.0
    )
    adjusted_upper = (
        min(1.0, exact_upper / (coverage_lower * sensitivity_lower))
        if coverage_lower > 0 and sensitivity_lower is not None and sensitivity_lower > 0
        else None
    )
    approximation = (
        min(1.0, 3.0 / (len(inspected) * coverage_lower * sensitivity_lower))
        if detected_events == 0
        and inspected
        and coverage_lower > 0
        and sensitivity_lower is not None
        and sensitivity_lower > 0
        else None
    )
    missed_injection = any(not item.caught for item in injected_positives)
    threshold_crossed = any(item.threshold_crossed for item in sensitivity_scenarios)
    detector_gate_status = (
        "blocked"
        if missed_injection
        else "indeterminate"
        if threshold_crossed or adjusted_upper is None
        else "bounded"
    )
    evidence_status = "complete" if len(inspected) == assigned else "ascertainment_incomplete"
    severity_counts: dict[str, int] = defaultdict(int)
    attribution_counts: dict[str, int] = defaultdict(int)
    for opportunity in opportunities:
        for incident in incidents_by_opportunity.get(opportunity.opportunity_id, ()):
            severity_counts[incident.severity] += 1
            attribution_counts[incident.attribution] += 1
    return SafetyStratumResult(
        gate_id=gate_id,
        stratum=stratum,
        history_mode=history_mode,
        assigned_denominator=assigned,
        included_denominator=sum(item.inclusion_status == "included" for item in opportunities),
        inspected_denominator=len(inspected),
        detected_events=detected_events,
        coverage_lower_bound=coverage_lower,
        sensitivity_lower_bound=sensitivity_lower,
        exact_detected_event_upper_bound=exact_upper,
        adjusted_harm_upper_bound=adjusted_upper,
        rule_of_three_approximation=approximation,
        detector_gate_status=detector_gate_status,
        evidence_status=evidence_status,
        severity_counts=tuple(sorted(severity_counts.items())),
        attribution_counts=tuple(sorted(attribution_counts.items())),
    )


def one_sided_clopper_pearson_upper(
    events: int, trials: int, confidence: float = _ONE_SIDED_CONFIDENCE
) -> float:
    """Return the exact one-sided Clopper-Pearson upper bound by binomial inversion."""

    _validate_binomial_input(events, trials, confidence)
    if events == trials:
        return 1.0
    alpha = 1.0 - confidence
    if events == 0:
        return 1.0 - alpha ** (1.0 / trials)
    low, high = 0.0, 1.0
    for _ in range(100):
        midpoint = (low + high) / 2
        if _binomial_cdf(events, trials, midpoint) > alpha:
            low = midpoint
        else:
            high = midpoint
    return high


def one_sided_clopper_pearson_lower(
    successes: int, trials: int, confidence: float = _ONE_SIDED_CONFIDENCE
) -> float:
    """Return the exact one-sided Clopper-Pearson lower bound by binomial inversion."""

    _validate_binomial_input(successes, trials, confidence)
    if successes == 0:
        return 0.0
    alpha = 1.0 - confidence
    if successes == trials:
        return alpha ** (1.0 / trials)
    low, high = 0.0, 1.0
    for _ in range(100):
        midpoint = (low + high) / 2
        if 1.0 - _binomial_cdf(successes - 1, trials, midpoint) < alpha:
            low = midpoint
        else:
            high = midpoint
    return low


def _binomial_cdf(events: int, trials: int, probability: float) -> float:
    if probability <= 0:
        return 1.0
    if probability >= 1:
        return 1.0 if events == trials else 0.0
    term = (1.0 - probability) ** trials
    total = term
    for value in range(1, events + 1):
        term *= (trials - value + 1) * probability / (value * (1.0 - probability))
        total += term
    return min(1.0, total)


def _validate_binomial_input(successes: int, trials: int, confidence: float) -> None:
    if (
        not isinstance(successes, int)
        or isinstance(successes, bool)
        or not 0 <= successes <= trials
    ):
        raise SafetyAnalysisError("binomial_event_count_invalid")
    if not isinstance(trials, int) or isinstance(trials, bool) or trials <= 0:
        raise SafetyAnalysisError("binomial_denominator_invalid")
    if not math.isfinite(confidence) or not 0 < confidence < 1:
        raise SafetyAnalysisError("binomial_confidence_invalid")


def _require_unique(values: object, code: str) -> None:
    materialized = tuple(values)  # type: ignore[arg-type]
    if len(materialized) != len(set(materialized)):
        raise SafetyAnalysisError(code)


def _require_evidence(values: tuple[str, ...], code: str) -> None:
    if not values or len(values) != len(set(values)):
        raise SafetyAnalysisError(code)
    for value in values:
        _require_sha256(value, code)


def _require_sha256(value: str, code: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or not value.isascii()
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SafetyAnalysisError(code)
