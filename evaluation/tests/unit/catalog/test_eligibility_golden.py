"""Golden stability tests for eligibility disposition output (Story 1.4, AC3-AC4).

Each `<name>.input.json` describes one `evaluate_task_eligibility` call; the
checked `<name>.expected.json` is the exact canonical, digest-attached
disposition. A regression in disposition shape, code ordering, or digest
computation changes these bytes and must be reviewed deliberately.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from memrelay_eval.catalog.eligibility import evaluate_task_eligibility
from memrelay_eval.catalog.fixtures import FixtureVerificationResult

GOLDEN_ROOT = Path(__file__).parents[2] / "golden" / "eligibility"


def _fixture_result(raw: dict[str, object]) -> FixtureVerificationResult:
    return FixtureVerificationResult(
        fixture_id="",
        verified=bool(raw["verified"]),
        codes=(),
        message="",
        resolved_sha256=raw.get("resolved_sha256"),
        provenance=raw.get("provenance"),
        data_classification=None,
    )


def _evaluate_from_input(input_path: Path) -> dict[str, object]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    fixture_results = {
        fixture_id: _fixture_result(raw) for fixture_id, raw in payload["fixture_results"].items()
    }
    return evaluate_task_eligibility(
        catalog_id=payload["catalog_id"],
        scenario_id=payload["scenario_id"],
        scenario_data_classification=payload["scenario_data_classification"],
        fixture_refs=payload["fixture_refs"],
        fixture_results=fixture_results,
        study_validity_ref=payload["study_validity_ref"],
        study_validity_records=payload["study_validity_records"],
    )


@pytest.mark.parametrize("name", ["eligible-synthetic", "rejected-multiple-failures"])
def test_eligibility_disposition_is_golden_stable(name: str) -> None:
    disposition = _evaluate_from_input(GOLDEN_ROOT / f"{name}.input.json")
    expected = json.loads((GOLDEN_ROOT / f"{name}.expected.json").read_text(encoding="utf-8"))

    assert disposition == expected
