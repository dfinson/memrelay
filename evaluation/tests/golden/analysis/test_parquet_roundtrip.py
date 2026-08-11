from __future__ import annotations

import pyarrow.parquet as pq
from memrelay_eval.evidence.parquet import ParquetMaterializer
from tests.integration.analysis.test_parquet_materialization import _record


def test_golden_reconciled_terminal_dataset_is_byte_stable_across_fresh_publications(
    tmp_path,
) -> None:
    store, record = _record(tmp_path)

    first = ParquetMaterializer(store, tmp_path / "parquet-one").materialize((record,))
    second = ParquetMaterializer(store, tmp_path / "parquet-two").materialize((record,))

    for name in ("assigned_units.parquet", "eligible_outcomes.parquet"):
        first_path = first.directory / name
        second_path = second.directory / name
        assert first_path.read_bytes() == second_path.read_bytes()
        second_rows = pq.ParquetFile(second_path).read().to_pylist()
        assert pq.read_table(first_path).to_pylist() == second_rows
