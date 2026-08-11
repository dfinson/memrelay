from __future__ import annotations

from pathlib import Path


def test_duckdb_analysis_has_no_operational_database_or_external_parquet_query_path() -> None:
    source = (
        Path(__file__).parents[2] / "src" / "memrelay_eval" / "analysis" / "queries.py"
    ).read_text(encoding="utf-8")

    assert "SqliteLedger" not in source
    assert "sqlite" not in source.casefold()
    assert "read_parquet(" not in source
    assert "ATTACH " not in source
    assert "COPY " not in source
