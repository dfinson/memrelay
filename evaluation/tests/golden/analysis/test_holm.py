from __future__ import annotations

import json
from pathlib import Path

import pytest
from memrelay_eval.analysis.multiplicity import EndpointPValue, holm_fwer
from tests.unit.analysis.test_multiplicity_gates_power import _bundle


def test_holm_reordered_tie_golden_vector() -> None:
    golden_path = Path(__file__).parent / "holm" / "reordered-ties.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    family = _bundle().family
    assert list(family.endpoint_ids) == golden["family_endpoint_ids"]
    assert family.method == golden["method"]
    assert family.method_version == golden["method_version"]

    results = holm_fwer(
        family,
        tuple(
            EndpointPValue(endpoint_id, p_value)
            for endpoint_id, p_value in golden["inputs"].items()
        ),
    )

    for result in results:
        expected = golden["expected"][result.endpoint_id]
        assert result.adjusted_p_value == pytest.approx(expected["adjusted_p_value"])
        assert result.rank == expected["rank"]
