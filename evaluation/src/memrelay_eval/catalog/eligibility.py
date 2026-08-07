"""Task-level data and study-validity eligibility disposition (Story 1.4, AC3-AC4).

Every scenario/task receives one immutable eligible-or-rejected disposition. A
disposition's canonical hash is derived from the resolved fixture bytes and the
resolved study-validity record content, so any input change (a rehashed fixture, a
revised necessity score, a new canary hit) produces a new disposition identity; no
prior disposition can be silently reused. Eligibility evaluation never blocks
compilation by itself (only fixture verification in `fixtures.py` does that): a
`rejected` disposition is a recorded, evidenced governance signal for later stages
(Epic 3 grading/execution), not a compile failure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .canonical import attach_digest
from .fixtures import FixtureVerificationResult

ELIGIBILITY_SCHEMA_VERSION = "1.0.0"

#: Task/history-level data classifications AC3 authorizes for initial-study data.
AUTHORIZED_TASK_DATA_CLASSIFICATIONS = frozenset({"synthetic", "public-license-audited"})

#: Minimum memory-necessity score (0-4 rubric) required for an eligible disposition.
NECESSITY_MINIMUM_SCORE = 3


def evaluate_task_eligibility(
    *,
    catalog_id: str,
    scenario_id: str,
    scenario_data_classification: str,
    fixture_refs: Sequence[str],
    fixture_results: Mapping[str, FixtureVerificationResult],
    study_validity_ref: str,
    study_validity_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate one scenario's AC3 data eligibility and AC4 study-validity gates.

    Returns a fully digest-attached, immutable disposition record. `codes` lists
    every failing reason (not only the first); the disposition is `"eligible"`
    only when `codes` is empty.
    """

    codes: set[str] = set()
    reviewer_roles: set[str] = set()

    if scenario_data_classification not in AUTHORIZED_TASK_DATA_CLASSIFICATIONS:
        codes.add("TASK_DATA_CLASSIFICATION_PROHIBITED")

    fixture_sha256: dict[str, str | None] = {}
    for fixture_id in fixture_refs:
        result = fixture_results.get(fixture_id)
        if result is None or not result.verified:
            codes.add("FIXTURE_UNVERIFIED")
            fixture_sha256[fixture_id] = None
            continue
        fixture_sha256[fixture_id] = result.resolved_sha256
        if scenario_data_classification == "synthetic" and result.provenance != "synthetic":
            codes.add("FIXTURE_PROVENANCE_MISMATCH")
        if (
            scenario_data_classification == "public-license-audited"
            and result.provenance != "public"
        ):
            codes.add("FIXTURE_PROVENANCE_MISMATCH")

    study_validity = study_validity_records.get(study_validity_ref)
    study_validity_snapshot: Mapping[str, Any] = {}
    if study_validity is None:
        codes.add("STUDY_VALIDITY_UNRESOLVED")
    else:
        study_validity_snapshot = study_validity
        codes.update(_necessity_codes(study_validity, reviewer_roles))
        codes.update(_contamination_and_holdout_codes(study_validity))
        codes.update(_stability_codes(study_validity))

    reviewer_roles.discard("")
    disposition = "eligible" if not codes else "rejected"
    evidence_refs = sorted({study_validity_ref, *fixture_refs})

    return attach_digest(
        {
            "schema_version": ELIGIBILITY_SCHEMA_VERSION,
            "catalog_id": catalog_id,
            "scenario_id": scenario_id,
            "disposition": disposition,
            "codes": sorted(codes),
            "evidence_refs": evidence_refs,
            "reviewer_roles": sorted(reviewer_roles),
            "scenario_data_classification": scenario_data_classification,
            "fixture_sha256": dict(sorted(fixture_sha256.items())),
            "study_validity_ref": study_validity_ref,
            "study_validity_snapshot": dict(study_validity_snapshot),
            "unpaid_conformance": True,
        }
    )


def _necessity_codes(study_validity: Mapping[str, Any], reviewer_roles: set[str]) -> set[str]:
    codes: set[str] = set()
    necessity = study_validity.get("memory_necessity")
    if not isinstance(necessity, Mapping):
        return {"NECESSITY_INSUFFICIENT"}
    reviewer_role = necessity.get("reviewer_role")
    if isinstance(reviewer_role, str):
        reviewer_roles.add(reviewer_role)
    score = necessity.get("score")
    if not isinstance(score, int) or isinstance(score, bool) or score < NECESSITY_MINIMUM_SCORE:
        codes.add("NECESSITY_INSUFFICIENT")

    shortcut_audit = study_validity.get("shortcut_audit")
    if not isinstance(shortcut_audit, Mapping):
        codes.add("SHORTCUT_UNRESOLVED")
    else:
        audit_reviewer_role = shortcut_audit.get("reviewer_role")
        if isinstance(audit_reviewer_role, str):
            reviewer_roles.add(audit_reviewer_role)
        unresolved = shortcut_audit.get("unresolved_shortcuts")
        if not isinstance(unresolved, list) or unresolved:
            codes.add("SHORTCUT_UNRESOLVED")
    return codes


def _contamination_and_holdout_codes(study_validity: Mapping[str, Any]) -> set[str]:
    codes: set[str] = set()
    contamination = study_validity.get("contamination")
    if not isinstance(contamination, Mapping):
        codes.add("CANARY_CONTAMINATION")
        codes.add("CUTOFF_UNVERIFIED")
    else:
        canary_hits = contamination.get("canary_hits")
        if not isinstance(canary_hits, int) or isinstance(canary_hits, bool) or canary_hits != 0:
            codes.add("CANARY_CONTAMINATION")
        if contamination.get("cutoff_status") not in {"post_cutoff", "access_controlled"}:
            codes.add("CUTOFF_UNVERIFIED")

    holdout = study_validity.get("holdout")
    if not isinstance(holdout, Mapping) or holdout.get("overlap_detected") is not False:
        codes.add("HOLDOUT_OVERLAP")
    return codes


def _stability_codes(study_validity: Mapping[str, Any]) -> set[str]:
    codes: set[str] = set()
    baseline = study_validity.get("baseline_stability")
    if (
        not isinstance(baseline, Mapping)
        or not isinstance(baseline.get("runs"), int)
        or baseline.get("passes") != baseline.get("runs")
    ):
        codes.add("BASELINE_UNSTABLE")

    gold = study_validity.get("gold_stability")
    if (
        not isinstance(gold, Mapping)
        or not isinstance(gold.get("runs"), int)
        or gold.get("passes") != gold.get("runs")
    ):
        codes.add("GOLD_UNSTABLE")
    return codes
