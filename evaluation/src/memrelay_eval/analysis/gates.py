"""Immutable threshold policy and strict claim-gate decisions."""

from __future__ import annotations

from dataclasses import dataclass

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import AnalysisError

from .intervals import SimultaneousInterval, require_claim_eligible_interval
from .multiplicity import FrozenClaimFamily, HolmResult
from .power import (
    FinalInformationProof,
    FrozenPowerProtocol,
    PowerEvaluation,
    fixed_information_look,
)
from .preregistration import SealedClaimProtocol

_CLAIM_TYPES = {
    "reliability_benefit",
    "quality_benefit",
    "no_regression",
    "cost_superiority",
    "wall_superiority",
}


def _valid_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value.isascii()
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class FrozenThresholdPolicy:
    protocol_sha256: str
    family_sha256: str
    family_registration_sha256: str
    sealed_claim_protocol_sha256: str
    reliability_benefit: float = 0.05
    quality_benefit: float = 0.05
    noninferiority: float = -0.02
    superiority_ratio: float = 0.90
    economic_noninferiority_ratio: float = 1.10
    panel_gate_sha256: str | None = None

    def __post_init__(self) -> None:
        if (
            self.reliability_benefit != 0.05
            or self.quality_benefit != 0.05
            or self.noninferiority != -0.02
            or self.superiority_ratio != 0.90
            or self.economic_noninferiority_ratio != 1.10
        ):
            raise AnalysisError("claim_threshold_relaxation_forbidden")
        if not all(
            _valid_sha256(value)
            for value in (
                self.protocol_sha256,
                self.family_sha256,
                self.family_registration_sha256,
                self.sealed_claim_protocol_sha256,
            )
        ):
            raise AnalysisError("threshold_lineage_invalid")
        if self.panel_gate_sha256 is not None and not _valid_sha256(self.panel_gate_sha256):
            raise AnalysisError("panel_gate_lineage_invalid")

    @property
    def threshold_registration_sha256(self) -> str:
        return canonical_digest(self._registration_document())

    @property
    def threshold_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            **self._registration_document(),
            "family_sha256": self.family_sha256,
            "sealed_claim_protocol_sha256": self.sealed_claim_protocol_sha256,
        }

    def _registration_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "protocol_sha256": self.protocol_sha256,
            "family_registration_sha256": self.family_registration_sha256,
            "reliability_benefit": self.reliability_benefit,
            "quality_benefit": self.quality_benefit,
            "noninferiority": self.noninferiority,
            "superiority_ratio": self.superiority_ratio,
            "economic_noninferiority_ratio": self.economic_noninferiority_ratio,
            "panel_gate_sha256": self.panel_gate_sha256,
        }


@dataclass(frozen=True, slots=True)
class ClaimGateDecision:
    endpoint_id: str
    claim_type: str
    status: str
    gate_trace: tuple[str, ...]
    source_sha256: str
    derivation_sha256: str
    protocol_sha256: str
    family_sha256: str
    sealed_claim_protocol_sha256: str
    threshold_sha256: str
    power_sha256: str
    power_evaluation_sha256: str
    information_sha256: str | None
    panel_gate_sha256: str | None
    categorical_gates_sha256: str

    def __post_init__(self) -> None:
        if self.status not in {"pass", "fail", "blocked", "indeterminate", "estimation-only"}:
            raise AnalysisError("claim_decision_status_invalid")
        if self.claim_type not in _CLAIM_TYPES or not self.endpoint_id:
            raise AnalysisError("claim_decision_claim_invalid")
        lineage = (
            self.source_sha256,
            self.derivation_sha256,
            self.protocol_sha256,
            self.family_sha256,
            self.sealed_claim_protocol_sha256,
            self.threshold_sha256,
            self.power_sha256,
            self.power_evaluation_sha256,
            self.categorical_gates_sha256,
        )
        if not all(_valid_sha256(value) for value in lineage):
            raise AnalysisError("claim_decision_lineage_invalid")
        if self.panel_gate_sha256 is not None and not _valid_sha256(self.panel_gate_sha256):
            raise AnalysisError("claim_decision_panel_lineage_invalid")
        if self.information_sha256 is not None and not _valid_sha256(self.information_sha256):
            raise AnalysisError("claim_decision_information_lineage_invalid")

    @property
    def decision_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "claim_gate_decision",
            "endpoint_id": self.endpoint_id,
            "claim_type": self.claim_type,
            "status": self.status,
            "gate_trace": list(self.gate_trace),
            "source_sha256": self.source_sha256,
            "derivation_sha256": self.derivation_sha256,
            "protocol_sha256": self.protocol_sha256,
            "family_sha256": self.family_sha256,
            "sealed_claim_protocol_sha256": self.sealed_claim_protocol_sha256,
            "threshold_sha256": self.threshold_sha256,
            "power_sha256": self.power_sha256,
            "power_evaluation_sha256": self.power_evaluation_sha256,
            "information_sha256": self.information_sha256,
            "panel_gate_sha256": self.panel_gate_sha256,
            "categorical_gates_sha256": self.categorical_gates_sha256,
        }


def evaluate_claim(
    family: FrozenClaimFamily,
    thresholds: FrozenThresholdPolicy,
    holm: HolmResult,
    interval: SimultaneousInterval,
    *,
    claim_type: str,
    source_sha256: str,
    derivation_sha256: str,
    power_evaluation: PowerEvaluation,
    power_status: str | None = None,
    panel_gate_passed: bool = True,
    categorical_gates_passed: bool = True,
    reliability_interval: SimultaneousInterval | None = None,
    quality_interval: SimultaneousInterval | None = None,
    qualitative_scale: tuple[float, float] | None = None,
    categorical_gates_sha256: str = "0" * 64,
    power_protocol: FrozenPowerProtocol,
    sealed_claim_protocol: SealedClaimProtocol,
    information_proof: FinalInformationProof | None = None,
    efficiency_component_evidence: tuple[tuple[HolmResult, SimultaneousInterval], ...] = (),
) -> ClaimGateDecision:
    """Apply preregistered predicates without endpoint salvage or early efficacy."""

    if claim_type not in _CLAIM_TYPES:
        raise AnalysisError("claim_type_unsupported")
    if not all(
        _valid_sha256(value)
        for value in (source_sha256, derivation_sha256, categorical_gates_sha256)
    ):
        raise AnalysisError("claim_decision_lineage_invalid")
    if not isinstance(sealed_claim_protocol, SealedClaimProtocol):
        raise AnalysisError("sealed_claim_protocol_required")
    if (
        thresholds.family_sha256 != family.family_sha256
        or thresholds.protocol_sha256 != family.protocol_sha256
        or thresholds.family_registration_sha256 != family.family_registration_sha256
        or thresholds.sealed_claim_protocol_sha256
        != sealed_claim_protocol.sealed_claim_protocol_sha256
        or holm.family_sha256 != family.family_sha256
    ):
        raise AnalysisError("claim_gate_family_drift")
    _validate_sealed_claim_artifacts(family, thresholds, power_protocol, sealed_claim_protocol)
    if holm.endpoint_id != interval.endpoint_id:
        raise AnalysisError("claim_gate_endpoint_mismatch")
    if holm.endpoint_id not in family.endpoint_ids:
        raise AnalysisError("claim_gate_endpoint_unregistered")
    _validate_claim_binding(family, claim_type, holm.endpoint_id)
    if claim_type == "no_regression" and not _is_one_sided_noninferiority_interval(
        interval, family
    ):
        raise AnalysisError("no_regression_interval_incompatible")

    _validate_power_evaluation(family, power_protocol, power_evaluation)
    if holm.endpoint_id != power_protocol.power_endpoint_id:
        raise AnalysisError("claim_gate_power_endpoint_mismatch")
    trace: list[str] = [f"holm:{holm.rejection}", f"power:{power_evaluation.status}"]
    information_sha256, final_information = _final_information_state(
        family, power_protocol.power_sha256, power_protocol, information_proof, trace
    )
    interval_eligible = (
        _is_one_sided_noninferiority_interval(interval, family)
        if claim_type == "no_regression"
        else _is_two_sided_claim_interval(interval, family)
    )
    if holm.status != "estimated" or not interval_eligible or not final_information:
        status = "blocked"
    elif power_evaluation.status == "estimation_only":
        status = "estimation-only"
    elif power_evaluation.status != "pass" or not categorical_gates_passed:
        status = "blocked" if not categorical_gates_passed else "indeterminate"
    elif not holm.rejection:
        status = "fail"
    elif (
        interval.point_estimate is None
        or interval.lower is None
        or (claim_type != "no_regression" and interval.upper is None)
    ):
        status = "indeterminate"
    elif claim_type == "reliability_benefit":
        status = _decision(
            interval.point_estimate >= thresholds.reliability_benefit and interval.lower > 0.0,
            trace,
            "benefit",
        )
    elif claim_type == "quality_benefit":
        if thresholds.panel_gate_sha256 is None:
            status = "blocked"
            trace.append("panel:evidence_missing")
        else:
            status = _decision(
                qualitative_scale == (0.0, 1.0)
                and panel_gate_passed
                and interval.point_estimate >= thresholds.quality_benefit
                and interval.lower > 0.0,
                trace,
                "quality",
            )
    elif claim_type == "no_regression":
        status = _decision(interval.lower > thresholds.noninferiority, trace, "noninferiority")
    else:
        status = _economic_decision(
            family,
            thresholds,
            holm,
            interval,
            reliability_interval,
            quality_interval,
            efficiency_component_evidence,
            trace,
        )
    return ClaimGateDecision(
        interval.endpoint_id,
        claim_type,
        status,
        tuple(trace),
        source_sha256,
        derivation_sha256,
        family.protocol_sha256,
        family.family_sha256,
        sealed_claim_protocol.sealed_claim_protocol_sha256,
        thresholds.threshold_sha256,
        power_protocol.power_sha256,
        power_evaluation.evaluation_sha256,
        information_sha256,
        thresholds.panel_gate_sha256,
        categorical_gates_sha256,
    )


def _validate_power_evaluation(
    family: FrozenClaimFamily,
    power_protocol: FrozenPowerProtocol,
    power_evaluation: PowerEvaluation,
) -> None:
    """Bind the decision to immutable simulation evidence, not caller assertions."""

    if not isinstance(power_protocol, FrozenPowerProtocol) or not isinstance(
        power_evaluation, PowerEvaluation
    ):
        raise AnalysisError("claim_power_evaluation_required")
    if (
        power_protocol.protocol_sha256 != family.protocol_sha256
        or power_protocol.family_sha256 != family.family_sha256
        or power_evaluation.protocol_sha256 != power_protocol.protocol_sha256
        or power_evaluation.family_sha256 != power_protocol.family_sha256
        or power_evaluation.power_sha256 != power_protocol.power_sha256
    ):
        raise AnalysisError("claim_power_evaluation_lineage_invalid")
    power_evaluation.validate_against(power_protocol)


def _validate_sealed_claim_artifacts(
    family: FrozenClaimFamily,
    thresholds: FrozenThresholdPolicy,
    power_protocol: FrozenPowerProtocol,
    sealed_claim_protocol: SealedClaimProtocol,
) -> None:
    """Require the actual pre-enrollment registry, not a copied lineage hash."""

    if not isinstance(sealed_claim_protocol, SealedClaimProtocol):
        raise AnalysisError("sealed_claim_protocol_required")
    sealed_claim_protocol.require_family(
        family_id=family.family_id,
        protocol_sha256=family.protocol_sha256,
        assignment_plan_sha256=family.assignment_plan_sha256,
        estimator_registry_sha256=family.estimand_registry_sha256,
        family_registration_sha256=family.family_registration_sha256,
        sealed_claim_protocol_sha256=family.sealed_claim_protocol_sha256,
    )
    sealed_claim_protocol.require_threshold(
        family_id=family.family_id,
        family_registration_sha256=family.family_registration_sha256,
        threshold_registration_sha256=thresholds.threshold_registration_sha256,
        sealed_claim_protocol_sha256=thresholds.sealed_claim_protocol_sha256,
    )
    if power_protocol.sealed_claim_protocol is not sealed_claim_protocol:
        raise AnalysisError("sealed_claim_power_unregistered")
    power_protocol.validate_against_seal()


def _validate_claim_binding(family: FrozenClaimFamily, claim_type: str, endpoint_id: str) -> None:
    bindings = {
        "reliability_benefit": (
            family.mode in {"reliability", "dual"}
            and endpoint_id == "EP-PRIM-SUCCESS"
            and family.endpoint_scale(endpoint_id) == "difference"
        ),
        "quality_benefit": (
            endpoint_id == "EP-QUAL" and family.endpoint_scale(endpoint_id) == "difference"
        ),
        "cost_superiority": (
            family.mode in {"efficiency", "dual"}
            and endpoint_id == "EP-COST"
            and endpoint_id in family.selected_efficiency_endpoint_ids
            and family.endpoint_scale(endpoint_id) == "ratio"
        ),
        "wall_superiority": (
            family.mode in {"efficiency", "dual"}
            and endpoint_id == "EP-WALL"
            and endpoint_id in family.selected_efficiency_endpoint_ids
            and family.endpoint_scale(endpoint_id) == "ratio"
        ),
        "no_regression": (
            (
                (family.mode == "efficiency" and endpoint_id == "EP-SUCC-NI")
                or endpoint_id == "EP-HARM"
            )
            and family.endpoint_direction(endpoint_id) in {"non_inferiority", "harm"}
            and family.endpoint_scale(endpoint_id) == "difference"
        ),
    }
    if not bindings[claim_type]:
        raise AnalysisError("claim_gate_endpoint_mode_invalid")


def _final_information_state(
    family: FrozenClaimFamily,
    power_sha256: str,
    power_protocol: FrozenPowerProtocol,
    information_proof: FinalInformationProof | None,
    trace: list[str],
) -> tuple[str | None, bool]:
    if information_proof is None:
        trace.append("information:missing")
        return None, False
    if (
        power_protocol.protocol_sha256 != family.protocol_sha256
        or power_protocol.family_sha256 != family.family_sha256
        or power_protocol.power_sha256 != power_sha256
        or information_proof.protocol_sha256 != power_protocol.protocol_sha256
        or information_proof.power_sha256 != power_protocol.power_sha256
    ):
        raise AnalysisError("final_information_lineage_invalid")
    information_sha256 = information_proof.information_sha256
    try:
        fixed_information_look(
            power_protocol, observed_n=information_proof.observed_n, purpose="efficacy"
        )
    except AnalysisError:
        if not 0 <= information_proof.observed_n < power_protocol.fixed_n:
            raise
        trace.append("information:early")
        return information_sha256, False
    trace.append("information:final")
    return information_sha256, True


def _is_two_sided_claim_interval(interval: SimultaneousInterval, family: FrozenClaimFamily) -> bool:
    return (
        interval.status == "estimated"
        and interval.procedure == "holm-compatible"
        and interval.family_sha256 == family.family_sha256
        and interval.sidedness == "two-sided"
        and interval.confidence_level >= 0.95
    )


def _is_one_sided_noninferiority_interval(
    interval: SimultaneousInterval, family: FrozenClaimFamily
) -> bool:
    return (
        interval.status == "estimated"
        and interval.procedure == "holm-compatible"
        and interval.family_sha256 == family.family_sha256
        and interval.sidedness == "lower-one-sided"
        and interval.confidence_level == 0.975
    )


def _economic_decision(
    family: FrozenClaimFamily,
    thresholds: FrozenThresholdPolicy,
    holm: HolmResult,
    interval: SimultaneousInterval,
    reliability_interval: SimultaneousInterval | None,
    quality_interval: SimultaneousInterval | None,
    component_evidence: tuple[tuple[HolmResult, SimultaneousInterval], ...],
    trace: list[str],
) -> str:
    if reliability_interval is None or quality_interval is None:
        return "blocked"
    _require_auxiliary_interval(
        reliability_interval,
        family,
        "EP-PRIM-SUCCESS" if family.mode == "dual" else "EP-SUCC-NI",
    )
    _require_auxiliary_interval(quality_interval, family, "EP-QUAL")
    evidence = _economic_component_evidence(family, holm, interval, component_evidence)
    if evidence is None:
        return "blocked"
    all_components_pass = all(
        component_holm.status == "estimated"
        and component_holm.rejection
        and _is_two_sided_claim_interval(component_interval, family)
        and component_interval.point_estimate is not None
        and component_interval.upper is not None
        and component_interval.point_estimate <= thresholds.superiority_ratio
        and component_interval.upper < 1.0
        for component_holm, component_interval in evidence
    )
    return _decision(
        all_components_pass
        and reliability_interval.lower is not None
        and quality_interval.lower is not None
        and reliability_interval.lower > thresholds.noninferiority
        and quality_interval.lower > thresholds.noninferiority,
        trace,
        "economic",
    )


def _require_auxiliary_interval(
    interval: SimultaneousInterval, family: FrozenClaimFamily, endpoint_id: str
) -> None:
    if interval.endpoint_id != endpoint_id:
        raise AnalysisError("claim_gate_endpoint_mismatch")
    require_claim_eligible_interval(interval, family)
    if not _is_two_sided_claim_interval(interval, family):
        raise AnalysisError("claim_gate_interval_incompatible")


def _economic_component_evidence(
    family: FrozenClaimFamily,
    holm: HolmResult,
    interval: SimultaneousInterval,
    additional: tuple[tuple[HolmResult, SimultaneousInterval], ...],
) -> tuple[tuple[HolmResult, SimultaneousInterval], ...] | None:
    entries = ((holm, interval), *additional)
    expected = set(family.selected_efficiency_endpoint_ids)
    by_endpoint: dict[str, tuple[HolmResult, SimultaneousInterval]] = {}
    for component_holm, component_interval in entries:
        if (
            component_holm.endpoint_id != component_interval.endpoint_id
            or component_holm.family_sha256 != family.family_sha256
            or component_interval.family_sha256 != family.family_sha256
            or component_holm.endpoint_id not in expected
        ):
            raise AnalysisError("claim_gate_endpoint_mismatch")
        if component_holm.endpoint_id in by_endpoint:
            raise AnalysisError("claim_gate_endpoint_duplicate")
        by_endpoint[component_holm.endpoint_id] = (component_holm, component_interval)
    if set(by_endpoint) != expected:
        return None
    return tuple(
        by_endpoint[endpoint_id] for endpoint_id in family.selected_efficiency_endpoint_ids
    )


def _decision(passed: bool, trace: list[str], name: str) -> str:
    trace.append(f"{name}:{passed}")
    return "pass" if passed else "fail"


def release_fitness(
    target_decisions: tuple[ClaimGateDecision, ...],
    non_target_intervals: tuple[SimultaneousInterval, ...],
    family: FrozenClaimFamily,
    *,
    categorical_gates_passed: bool,
) -> bool:
    """Fail closed unless unique evidence covers every non-target family endpoint."""

    if not target_decisions or not categorical_gates_passed:
        return False
    if any(decision.family_sha256 != family.family_sha256 for decision in target_decisions):
        raise AnalysisError("release_fitness_family_drift")
    for decision in target_decisions:
        _validate_claim_binding(family, decision.claim_type, decision.endpoint_id)
    target_endpoint_ids = [decision.endpoint_id for decision in target_decisions]
    if (
        len(set(target_endpoint_ids)) != len(target_endpoint_ids)
        or any(endpoint_id not in family.endpoint_ids for endpoint_id in target_endpoint_ids)
        or any(
            decision.status == "pass" and decision.information_sha256 is None
            for decision in target_decisions
        )
        or not any(
            decision.status == "pass"
            and decision.claim_type
            in {
                "reliability_benefit",
                "quality_benefit",
                "cost_superiority",
                "wall_superiority",
            }
            for decision in target_decisions
        )
    ):
        return False
    if family.mode == "dual" and any(
        not any(
            decision.endpoint_id == endpoint_id and decision.status == "pass"
            for decision in target_decisions
        )
        for endpoint_id in family.selected_efficiency_endpoint_ids
    ):
        return False
    remaining = set(family.endpoint_ids) - set(target_endpoint_ids)
    by_endpoint = {interval.endpoint_id: interval for interval in non_target_intervals}
    if len(by_endpoint) != len(non_target_intervals) or set(by_endpoint) != remaining:
        return False
    for endpoint_id in family.endpoint_ids:
        if endpoint_id not in remaining:
            continue
        interval = by_endpoint[endpoint_id]
        try:
            require_claim_eligible_interval(interval, family)
        except AnalysisError:
            return False
        if family.endpoint_scale(endpoint_id) == "difference":
            if (
                interval.sidedness != "two-sided"
                or interval.confidence_level < 0.95
                or interval.lower is None
                or interval.lower <= -0.02
            ):
                return False
        else:
            if (
                interval.sidedness != "two-sided"
                or interval.confidence_level < 0.95
                or interval.upper is None
                or interval.upper >= 1.10
            ):
                return False
    return True
