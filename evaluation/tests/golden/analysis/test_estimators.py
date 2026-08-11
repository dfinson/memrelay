from __future__ import annotations

import json
from pathlib import Path

import pytest
from memrelay_eval.analysis.estimators import estimate_itt
from tests.unit.analysis.test_itt_outcomes import _table


def test_blocked_itt_golden_vector_is_deterministic() -> None:
    golden_path = Path(__file__).parent / "estimators" / "blocked-itt.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    estimate = estimate_itt(_table())

    assert estimate.status == "estimated"
    assert estimate.point_estimate == pytest.approx(golden["expected_difference"])
    assert estimate.p_value is not None
