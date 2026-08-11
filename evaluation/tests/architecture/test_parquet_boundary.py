from __future__ import annotations

from pathlib import Path


def test_parquet_materialization_has_no_ledger_or_analysis_state_dependency() -> None:
    source = (
        Path(__file__).parents[2] / "src" / "memrelay_eval" / "evidence" / "parquet.py"
    ).read_text(encoding="utf-8")

    assert "SqliteLedger" not in source
    assert "sqlite" not in source.casefold()
    assert "duckdb" not in source.casefold()
