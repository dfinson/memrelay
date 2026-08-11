from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from memrelay_eval.evidence.pricing import load_price_table


def test_committed_framework_openai_initial_table_has_frozen_rates() -> None:
    data = (
        Path(__file__).parents[3] / "catalog" / "prices" / "framework-openai-initial.json"
    ).read_bytes()
    table = load_price_table(data, expected_sha256=sha256(data).hexdigest())

    assert table.model == "gpt-4.1-mini-2025-04-14"
    assert table.scale == 1_000_000
    assert {item.unit: item.rate_per_scale for item in table.rates} == {
        "input_token": "0.4",
        "cached_input_token": "0.1",
        "output_token": "1.6",
    }
