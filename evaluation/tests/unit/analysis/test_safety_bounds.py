from __future__ import annotations

import math

import pytest
from memrelay_eval.analysis.safety import (
    DetectorInspection,
    HarmIncident,
    InjectedPositive,
    SafetyOpportunity,
    SafetyPolicy,
    SensitivityScenario,
    evaluate_safety,
    one_sided_clopper_pearson_lower,
    one_sided_clopper_pearson_upper,
)
from memrelay_eval.domain.errors import SafetyAnalysisError

_HASH = "a" * 64


def _policy() -> SafetyPolicy:
    return SafetyPolicy(
        policy_id="safety-policy-v1",
        detector_id="detector-v1",
        detector_version="1.0.0",
        injected_positive_plan_sha256=_HASH,
        sensitivity_model_sha256="b" * 64,
        threshold_sha256="c" * 64,
    )


def _opportunity(identifier: str, *, inclusion_status: str = "included") -> SafetyOpportunity:
    return SafetyOpportunity(
        opportunity_id=identifier,
        gate_id="SG-001",
        assignment_id=f"assignment-{identifier}",
        stratum="product",
        history_mode="controlled",
        inclusion_status=inclusion_status,
        evidence_sha256=("d" * 64,),
    )


def _injection(*, caught: bool) -> InjectedPositive:
    return InjectedPositive(
        injection_id="injection-1",
        detector_id="detector-v1",
        detector_version="1.0.0",
        caught=caught,
        plan_sha256=_HASH,
        evidence_sha256=("e" * 64,),
    )


def test_zero_event_exact_bound_retains_assigned_and_excluded_denominators() -> None:
    result = evaluate_safety(
        policy=_policy(),
        opportunities=(
            _opportunity("opportunity-1"),
            _opportunity("opportunity-2", inclusion_status="excluded"),
            _opportunity("opportunity-3"),
        ),
        inspections=(
            DetectorInspection(
                opportunity_id="opportunity-1",
                detector_id="detector-v1",
                detector_version="1.0.0",
                result="no_event",
                evidence_sha256=("f" * 64,),
            ),
            DetectorInspection(
                opportunity_id="opportunity-2",
                detector_id="detector-v1",
                detector_version="1.0.0",
                result="no_event",
                evidence_sha256=("0" * 64,),
            ),
            DetectorInspection(
                opportunity_id="opportunity-3",
                detector_id="detector-v1",
                detector_version="1.0.0",
                result="no_event",
                evidence_sha256=("1" * 64,),
            ),
        ),
        injected_positives=(_injection(caught=True),),
        source_manifest_sha256=("9" * 64,),
    )

    stratum = result.results[0]
    expected_exact = 1 - 0.05 ** (1 / 3)
    assert stratum.assigned_denominator == 3
    assert stratum.included_denominator == 2
    assert stratum.inspected_denominator == 3
    assert stratum.detected_events == 0
    assert stratum.exact_detected_event_upper_bound == pytest.approx(expected_exact)
    assert stratum.adjusted_harm_upper_bound == 1.0
    assert stratum.rule_of_three_approximation == 1.0
    assert stratum.detector_gate_status == "bounded"


def test_incomplete_ascertainment_is_reported_not_counted_as_absence() -> None:
    result = evaluate_safety(
        policy=_policy(),
        opportunities=(_opportunity("opportunity-1"), _opportunity("opportunity-2")),
        inspections=(
            DetectorInspection(
                opportunity_id="opportunity-1",
                detector_id="detector-v1",
                detector_version="1.0.0",
                result="no_event",
                evidence_sha256=("f" * 64,),
            ),
        ),
        injected_positives=(_injection(caught=True),),
        source_manifest_sha256=("9" * 64,),
    )

    stratum = result.results[0]
    assert stratum.inspected_denominator == 1
    assert stratum.evidence_status == "ascertainment_incomplete"
    assert stratum.coverage_lower_bound > 0
    assert stratum.adjusted_harm_upper_bound == 1.0


def test_zero_inspections_are_reported_as_indeterminate_with_maximal_bound() -> None:
    result = evaluate_safety(
        policy=_policy(),
        opportunities=(_opportunity("opportunity-1"),),
        inspections=(),
        injected_positives=(_injection(caught=True),),
        source_manifest_sha256=("9" * 64,),
    )

    stratum = result.results[0]
    assert stratum.inspected_denominator == 0
    assert stratum.coverage_lower_bound == 0
    assert stratum.exact_detected_event_upper_bound == 1.0
    assert stratum.adjusted_harm_upper_bound is None
    assert stratum.detector_gate_status == "indeterminate"


def test_missed_injected_positive_and_threshold_crossing_are_not_overridden() -> None:
    missed = evaluate_safety(
        policy=_policy(),
        opportunities=(_opportunity("opportunity-1"),),
        inspections=(
            DetectorInspection(
                opportunity_id="opportunity-1",
                detector_id="detector-v1",
                detector_version="1.0.0",
                result="no_event",
                evidence_sha256=("f" * 64,),
            ),
        ),
        injected_positives=(_injection(caught=False),),
        source_manifest_sha256=("9" * 64,),
    )
    threshold_crossing = evaluate_safety(
        policy=_policy(),
        opportunities=(_opportunity("opportunity-1"),),
        inspections=(
            DetectorInspection(
                opportunity_id="opportunity-1",
                detector_id="detector-v1",
                detector_version="1.0.0",
                result="no_event",
                evidence_sha256=("f" * 64,),
            ),
        ),
        injected_positives=(_injection(caught=True),),
        sensitivity_scenarios=(
            SensitivityScenario(
                scenario_id="max-adverse",
                model_sha256="b" * 64,
                threshold_crossed=True,
                evidence_sha256=("2" * 64,),
            ),
        ),
        source_manifest_sha256=("9" * 64,),
    )

    assert missed.results[0].detector_gate_status == "blocked"
    assert threshold_crossing.results[0].detector_gate_status == "indeterminate"


def test_nonzero_events_preserve_harm_tail_and_exact_bounds() -> None:
    result = evaluate_safety(
        policy=_policy(),
        opportunities=(_opportunity("opportunity-1"), _opportunity("opportunity-2")),
        inspections=(
            DetectorInspection(
                opportunity_id="opportunity-1",
                detector_id="detector-v1",
                detector_version="1.0.0",
                result="event",
                evidence_sha256=("f" * 64,),
            ),
            DetectorInspection(
                opportunity_id="opportunity-2",
                detector_id="detector-v1",
                detector_version="1.0.0",
                result="no_event",
                evidence_sha256=("0" * 64,),
            ),
        ),
        injected_positives=(_injection(caught=True),),
        incidents=(
            HarmIncident(
                incident_id="incident-1",
                opportunity_id="opportunity-1",
                severity="high",
                attribution="possible",
                evidence_sha256=("1" * 64,),
            ),
        ),
        source_manifest_sha256=("9" * 64,),
    )

    stratum = result.results[0]
    assert stratum.detected_events == 1
    assert stratum.severity_counts == (("high", 1),)
    assert stratum.attribution_counts == (("possible", 1),)
    assert 0 < stratum.exact_detected_event_upper_bound < 1


def test_exact_bounds_reject_zero_denominator_and_do_not_use_rule_of_three_as_exact() -> None:
    with pytest.raises(SafetyAnalysisError, match="binomial denominator invalid"):
        one_sided_clopper_pearson_upper(0, 0)

    assert one_sided_clopper_pearson_lower(3, 3) == pytest.approx(0.05 ** (1 / 3))
    assert one_sided_clopper_pearson_upper(0, 10) == pytest.approx(1 - 0.05**0.1)
    assert not math.isclose(
        one_sided_clopper_pearson_upper(0, 10),
        3 / 10,
        rel_tol=1e-5,
    )


def test_harm_event_without_immutable_tail_evidence_fails_closed() -> None:
    with pytest.raises(SafetyAnalysisError, match="detected harm tail missing"):
        evaluate_safety(
            policy=_policy(),
            opportunities=(_opportunity("opportunity-1"),),
            inspections=(
                DetectorInspection(
                    opportunity_id="opportunity-1",
                    detector_id="detector-v1",
                    detector_version="1.0.0",
                    result="event",
                    evidence_sha256=("f" * 64,),
                ),
            ),
            injected_positives=(_injection(caught=True),),
            source_manifest_sha256=("9" * 64,),
        )


def test_safety_lineage_changes_when_source_evidence_changes() -> None:
    policy = _policy()
    first = evaluate_safety(
        policy=policy,
        opportunities=(_opportunity("opportunity-1"),),
        inspections=(
            DetectorInspection(
                opportunity_id="opportunity-1",
                detector_id="detector-v1",
                detector_version="1.0.0",
                result="no_event",
                evidence_sha256=("f" * 64,),
            ),
        ),
        injected_positives=(_injection(caught=True),),
        source_manifest_sha256=("9" * 64,),
    )
    changed = evaluate_safety(
        policy=policy,
        opportunities=(
            SafetyOpportunity(
                opportunity_id="opportunity-1",
                gate_id="SG-001",
                assignment_id="assignment-opportunity-1",
                stratum="product",
                history_mode="controlled",
                inclusion_status="included",
                evidence_sha256=("8" * 64,),
            ),
        ),
        inspections=(
            DetectorInspection(
                opportunity_id="opportunity-1",
                detector_id="detector-v1",
                detector_version="1.0.0",
                result="no_event",
                evidence_sha256=("f" * 64,),
            ),
        ),
        injected_positives=(_injection(caught=True),),
        source_manifest_sha256=("9" * 64,),
    )

    assert first.derivation_sha256 != changed.derivation_sha256
    assert first.digest != changed.digest
