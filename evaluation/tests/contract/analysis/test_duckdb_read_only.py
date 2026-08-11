from __future__ import annotations

import json
from dataclasses import replace

import duckdb
import pyarrow as pa
import pytest
from memrelay_eval.analysis.queries import (
    AnalysisQuery,
    DerivationPublisher,
    DerivationSpec,
    FrozenDataset,
    MaterializedAnalysisDataset,
    ReadOnlyDuckDbAnalysis,
    read_materialized_dataset,
)
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.cli.main import main
from memrelay_eval.domain.errors import AnalysisError
from memrelay_eval.evidence.parquet import ParquetMaterializer
from tests.integration.analysis.test_parquet_materialization import _record


def _dataset(tmp_path) -> FrozenDataset:
    store, record = _record(tmp_path)
    result = ParquetMaterializer(store, tmp_path / "parquet").materialize((record,))
    return FrozenDataset.open(tmp_path / "parquet", result.dataset_version)


def test_only_a_named_immutable_dataset_version_can_be_opened(tmp_path) -> None:
    dataset = _dataset(tmp_path)

    with pytest.raises(AnalysisError) as error:
        FrozenDataset.open(dataset.root, "latest")

    assert error.value.code == "mutable_or_invalid_dataset_version"


def test_verified_parquet_is_registered_in_memory_without_a_public_sql_escape_hatch(
    tmp_path,
) -> None:
    dataset = _dataset(tmp_path)

    with ReadOnlyDuckDbAnalysis(dataset) as analysis:
        assert not hasattr(analysis, "execute")
        result = analysis.read(
            AnalysisQuery(
                "assigned_units",
                columns=("assignment_id", "sequence_id", "model_id"),
            )
        )
        with pytest.raises(duckdb.Error):
            analysis._connection.execute("SELECT * FROM read_parquet('outside.parquet')")

    assert result.num_rows == 1
    assert result.column_names == ["assignment_id", "sequence_id", "model_id"]


def test_schema_or_bytes_drift_blocks_registration_before_duckdb_opens(tmp_path) -> None:
    dataset = _dataset(tmp_path)
    assigned = dataset.directory / "assigned_units.parquet"
    assigned.write_bytes(b"not parquet")

    with pytest.raises(AnalysisError) as error:
        FrozenDataset.open(dataset.root, dataset.dataset_version)

    assert error.value.code == "dataset_table_hash_conflict"


def test_frozen_itt_reader_and_duckdb_share_the_verified_dataset_boundary(tmp_path) -> None:
    dataset = _dataset(tmp_path)

    materialized = read_materialized_dataset(dataset.directory)
    with ReadOnlyDuckDbAnalysis.open(dataset.root, dataset.dataset_version) as analysis:
        assigned = analysis.read(AnalysisQuery("assigned_units"))
        outcomes = analysis.read(AnalysisQuery("eligible_outcomes"))

    assert isinstance(materialized, MaterializedAnalysisDataset)
    assert materialized.dataset_manifest_sha256 == dataset.manifest_sha256
    assert materialized.assigned_units == assigned.to_pylist()
    assert materialized.eligible_outcomes == outcomes.to_pylist()


def test_table_hash_tampering_fails_before_frozen_or_duckdb_registration(tmp_path) -> None:
    dataset = _dataset(tmp_path)
    (dataset.directory / "assigned_units.parquet").write_bytes(b"tampered")

    with pytest.raises(AnalysisError) as frozen:
        read_materialized_dataset(dataset.directory)
    with pytest.raises(AnalysisError) as duckdb_open:
        ReadOnlyDuckDbAnalysis.open(dataset.root, dataset.dataset_version)

    assert frozen.value.code == duckdb_open.value.code == "dataset_table_hash_conflict"


def test_aggregate_requires_every_varying_governed_dimension(tmp_path) -> None:
    dataset = _dataset(tmp_path)
    assigned = dataset.tables["assigned_units"]
    altered = assigned.to_pylist()[0]
    altered["assignment_id"] = "assignment_" + "d" * 32
    altered["analysis_unit_id"] = "analysis_" + "d" * 32
    altered["run_id"] = "run_" + "d" * 32
    altered["attempt_id"] = "attempt_" + "d" * 32
    altered["model_id"] = "model_" + "d" * 32
    two_models = pa.concat_tables((assigned, pa.Table.from_pylist([altered], assigned.schema)))
    mixed = replace(dataset, tables={**dataset.tables, "assigned_units": two_models})

    with ReadOnlyDuckDbAnalysis(mixed) as analysis:
        with pytest.raises(AnalysisError) as error:
            analysis.count_by("assigned_units", ("stratum", "history_mode"))
        assert error.value.code == "explicit_stratified_operation_required"
        assert error.value.fields == ("model_id",)

        counted = analysis.count_by("assigned_units", ("stratum", "history_mode", "model_id"))

    assert counted.num_rows == 2


def test_derivations_and_figures_have_exact_reproducible_hashes(tmp_path) -> None:
    dataset = _dataset(tmp_path)
    with ReadOnlyDuckDbAnalysis(dataset) as analysis:
        table = analysis.read(
            AnalysisQuery("eligible_outcomes", columns=("endpoint_id", "numeric_value"))
        )
    publisher = DerivationPublisher(tmp_path / "derived", dataset)
    table_spec = DerivationSpec(
        "endpoint-values",
        "table",
        gate_ids=("gate_a",),
        query_sha256="a" * 64,
    )
    figure_spec = DerivationSpec("endpoint-values-figure", "figure", gate_ids=("gate_a",))

    first = publisher.publish_table(table, table_spec)
    second = publisher.publish_table(table, table_spec)
    figure_one = publisher.publish_figure(table, ("endpoint_id", "numeric_value"), figure_spec)
    figure_two = publisher.publish_figure(table, ("endpoint_id", "numeric_value"), figure_spec)

    assert first.derivation_sha256 == second.derivation_sha256
    assert first.output_path.read_bytes() == second.output_path.read_bytes()
    assert json.loads(first.manifest_path.read_text(encoding="utf-8"))["spec"]["query_sha256"] == (
        "a" * 64
    )
    assert figure_one.derivation_sha256 == figure_two.derivation_sha256
    assert figure_one.output_path.read_bytes() == figure_two.output_path.read_bytes()
    assert b"DejaVu Sans" in figure_one.output_path.read_bytes()


def test_derivation_publication_refuses_to_write_beside_source_parquet(tmp_path) -> None:
    dataset = _dataset(tmp_path)

    with pytest.raises(AnalysisError) as error:
        DerivationPublisher(dataset.root / "derived", dataset)

    assert error.value.code == "derivation_output_overlaps_immutable_dataset"


def test_cli_requires_a_canonical_explicit_dataset_plan(tmp_path, capsys) -> None:
    dataset = _dataset(tmp_path)
    plan = {
        "schema_version": "1.0.0",
        "stage": "unit",
        "dataset_version": dataset.dataset_version,
        "table": "eligible_outcomes",
        "columns": ["endpoint_id", "numeric_value"],
        "equals": [],
        "derivation_name": "cli-endpoint-values",
        "derivation_kind": "table",
        "gate_ids": ["gate_a"],
        "parent_derivations": [],
    }
    plan_path = tmp_path / "analysis-plan.json"
    plan_path.write_bytes(canonical_bytes(plan))

    status = main(
        (
            "analyze",
            "--stage",
            "unit",
            "--parquet-root",
            str(dataset.root),
            "--dataset-version",
            dataset.dataset_version,
            "--plan",
            str(plan_path),
            "--output-root",
            str(tmp_path / "derived"),
        )
    )

    assert status == 0
    assert "derivation_sha256" in capsys.readouterr().out
