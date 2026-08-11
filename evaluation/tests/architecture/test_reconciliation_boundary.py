from __future__ import annotations

from pathlib import Path


def test_reconciliation_uses_only_domain_ports_and_never_opens_sqlite_or_analysis() -> None:
    source = (
        Path(__file__).parents[2] / "src" / "memrelay_eval" / "evidence" / "reconcile.py"
    ).read_text(encoding="utf-8")

    assert "ArtifactStorePort" in source
    assert "LedgerPort" in source
    assert "sqlite3" not in source
    assert "duckdb" not in source
    assert "ledger.repository" not in source
