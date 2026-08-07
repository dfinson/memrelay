"""Unit tests for task/study-validity eligibility disposition (Story 1.4, AC3-AC4).

Matrix covers the story's Testing Requirements: synthetic/public-approved
eligibility, every AC3-prohibited class, and every AC4 scientific-validity
failure (necessity, shortcut, canary, holdout, baseline, gold), plus the
immutable-identity requirement that changed inputs cannot reuse a disposition.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from memrelay_eval.catalog.eligibility import (
    AUTHORIZED_TASK_DATA_CLASSIFICATIONS,
    evaluate_task_eligibility,
)
from memrelay_eval.catalog.fixtures import FixtureVerificationResult

FIXTURE_ID = "fixture_cccccccccccccccccccccccccccccccc"
STUDY_VALIDITY_REF = "studyvalidity_99999999999999999999999999999999"


def verified_fixture(
    sha256: str = "a" * 64, provenance: str = "synthetic"
) -> FixtureVerificationResult:
    return FixtureVerificationResult(
        fixture_id=FIXTURE_ID,
        verified=True,
        codes=(),
        message="fixture verified",
        resolved_sha256=sha256,
        provenance=provenance,
        data_classification="synthetic" if provenance == "synthetic" else "public-license-audited",
    )


def passing_study_validity() -> dict[str, object]:
    return {
        "id": STUDY_VALIDITY_REF,
        "memory_necessity": {
            "reviewer_role": "evaluation-scientist",
            "score": 4,
            "rationale": "Requires cross-session recall",
        },
        "shortcut_audit": {
            "reviewer_role": "evaluation-scientist",
            "routes_examined": ["Direct answer lookup"],
            "unresolved_shortcuts": [],
        },
        "contamination": {
            "canary_id": "canary_marker",
            "canary_hits": 0,
            "cutoff_status": "post_cutoff",
        },
        "holdout": {"segment": "confirmatory", "overlap_detected": False},
        "baseline_stability": {"runs": 3, "passes": 3},
        "gold_stability": {"runs": 3, "passes": 3},
    }


def evaluate(
    *,
    scenario_data_classification: str = "synthetic",
    fixture_results: dict[str, FixtureVerificationResult] | None = None,
    study_validity: dict[str, object] | None = None,
) -> dict[str, object]:
    resolved_fixture_results = (
        {FIXTURE_ID: verified_fixture()} if fixture_results is None else fixture_results
    )
    resolved_study_validity = passing_study_validity() if study_validity is None else study_validity
    return evaluate_task_eligibility(
        catalog_id="cat_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        scenario_id="scenario_55555555555555555555555555555555",
        scenario_data_classification=scenario_data_classification,
        fixture_refs=[FIXTURE_ID],
        fixture_results=resolved_fixture_results,
        study_validity_ref=STUDY_VALIDITY_REF,
        study_validity_records={STUDY_VALIDITY_REF: resolved_study_validity},
    )


def test_synthetic_data_with_passing_study_validity_is_eligible() -> None:
    disposition = evaluate(scenario_data_classification="synthetic")

    assert disposition["disposition"] == "eligible"
    assert disposition["codes"] == []
    assert "digest" in disposition


def test_public_license_audited_data_with_matching_provenance_is_eligible() -> None:
    disposition = evaluate(
        scenario_data_classification="public-license-audited",
        fixture_results={FIXTURE_ID: verified_fixture(provenance="public")},
    )

    assert disposition["disposition"] == "eligible"
    assert disposition["codes"] == []


@pytest.mark.parametrize(
    "prohibited_classification",
    ["private", "personal", "proprietary", "credential", "unclassified"],
)
def test_every_ac3_prohibited_classification_is_rejected(prohibited_classification: str) -> None:
    assert prohibited_classification not in AUTHORIZED_TASK_DATA_CLASSIFICATIONS

    disposition = evaluate(scenario_data_classification=prohibited_classification)

    assert disposition["disposition"] == "rejected"
    assert "TASK_DATA_CLASSIFICATION_PROHIBITED" in disposition["codes"]


def test_unverified_fixture_is_rejected() -> None:
    unverified = FixtureVerificationResult(
        fixture_id=FIXTURE_ID,
        verified=False,
        codes=("FIXTURE_HASH_MISMATCH",),
        message="fixture_id: FIXTURE_HASH_MISMATCH",
        resolved_sha256=None,
        provenance=None,
        data_classification=None,
    )

    disposition = evaluate(fixture_results={FIXTURE_ID: unverified})

    assert disposition["disposition"] == "rejected"
    assert "FIXTURE_UNVERIFIED" in disposition["codes"]


def test_fixture_provenance_mismatch_is_rejected() -> None:
    disposition = evaluate(
        scenario_data_classification="synthetic",
        fixture_results={FIXTURE_ID: verified_fixture(provenance="public")},
    )

    assert disposition["disposition"] == "rejected"
    assert "FIXTURE_PROVENANCE_MISMATCH" in disposition["codes"]


def test_unresolved_study_validity_reference_is_rejected() -> None:
    disposition = evaluate_task_eligibility(
        catalog_id="cat_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        scenario_id="scenario_55555555555555555555555555555555",
        scenario_data_classification="synthetic",
        fixture_refs=[FIXTURE_ID],
        fixture_results={FIXTURE_ID: verified_fixture()},
        study_validity_ref="studyvalidity_missing",
        study_validity_records={},
    )

    assert disposition["disposition"] == "rejected"
    assert "STUDY_VALIDITY_UNRESOLVED" in disposition["codes"]


def test_necessity_score_below_minimum_is_rejected() -> None:
    study_validity = passing_study_validity()
    study_validity["memory_necessity"]["score"] = 2

    disposition = evaluate(study_validity=study_validity)

    assert disposition["disposition"] == "rejected"
    assert "NECESSITY_INSUFFICIENT" in disposition["codes"]


def test_unresolved_shortcut_is_rejected() -> None:
    study_validity = passing_study_validity()
    study_validity["shortcut_audit"]["unresolved_shortcuts"] = ["identifier leakage"]

    disposition = evaluate(study_validity=study_validity)

    assert disposition["disposition"] == "rejected"
    assert "SHORTCUT_UNRESOLVED" in disposition["codes"]


def test_canary_hit_causes_contamination_rejection() -> None:
    study_validity = passing_study_validity()
    study_validity["contamination"]["canary_hits"] = 1

    disposition = evaluate(study_validity=study_validity)

    assert disposition["disposition"] == "rejected"
    assert "CANARY_CONTAMINATION" in disposition["codes"]


def test_unavailable_cutoff_status_is_rejected() -> None:
    study_validity = passing_study_validity()
    study_validity["contamination"]["cutoff_status"] = "unavailable"

    disposition = evaluate(study_validity=study_validity)

    assert disposition["disposition"] == "rejected"
    assert "CUTOFF_UNVERIFIED" in disposition["codes"]


def test_holdout_overlap_is_rejected() -> None:
    study_validity = passing_study_validity()
    study_validity["holdout"]["overlap_detected"] = True

    disposition = evaluate(study_validity=study_validity)

    assert disposition["disposition"] == "rejected"
    assert "HOLDOUT_OVERLAP" in disposition["codes"]


def test_baseline_instability_is_rejected() -> None:
    study_validity = passing_study_validity()
    study_validity["baseline_stability"]["passes"] = 2

    disposition = evaluate(study_validity=study_validity)

    assert disposition["disposition"] == "rejected"
    assert "BASELINE_UNSTABLE" in disposition["codes"]


def test_gold_grader_instability_is_rejected() -> None:
    study_validity = passing_study_validity()
    study_validity["gold_stability"]["passes"] = 2

    disposition = evaluate(study_validity=study_validity)

    assert disposition["disposition"] == "rejected"
    assert "GOLD_UNSTABLE" in disposition["codes"]


def test_multiple_simultaneous_failures_are_all_reported() -> None:
    study_validity = passing_study_validity()
    study_validity["contamination"]["canary_hits"] = 3
    study_validity["holdout"]["overlap_detected"] = True

    disposition = evaluate(scenario_data_classification="private", study_validity=study_validity)

    assert disposition["disposition"] == "rejected"
    assert {
        "TASK_DATA_CLASSIFICATION_PROHIBITED",
        "CANARY_CONTAMINATION",
        "HOLDOUT_OVERLAP",
    }.issubset(set(disposition["codes"]))


def test_changed_fixture_bytes_produce_a_new_disposition_identity() -> None:
    baseline = evaluate(fixture_results={FIXTURE_ID: verified_fixture(sha256="a" * 64)})
    rehashed = evaluate(fixture_results={FIXTURE_ID: verified_fixture(sha256="b" * 64)})

    assert baseline["disposition"] == "eligible"
    assert rehashed["disposition"] == "eligible"
    assert baseline["digest"] != rehashed["digest"]
    assert baseline["fixture_sha256"] != rehashed["fixture_sha256"]


def test_changed_study_validity_content_produces_a_new_disposition_identity() -> None:
    baseline = evaluate()
    changed_study_validity = deepcopy(passing_study_validity())
    changed_study_validity["memory_necessity"]["rationale"] = "A revised rationale"
    changed = evaluate(study_validity=changed_study_validity)

    assert baseline["disposition"] == "eligible"
    assert changed["disposition"] == "eligible"
    assert baseline["digest"] != changed["digest"]


def test_disposition_is_deterministic_for_identical_inputs() -> None:
    first = evaluate()
    second = evaluate()

    assert first == second
    assert first["digest"] == second["digest"]
