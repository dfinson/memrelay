from __future__ import annotations

import json
from pathlib import Path

from memrelay_eval.analysis.replay import (
    ReplayOutputs,
    ReproductionBundle,
    compare_replay_outputs,
    rebuild_retained_outputs,
)
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.cli.main import main
from memrelay_eval.evidence.parquet import ParquetMaterializer
from tests.integration.analysis.test_parquet_materialization import _record


def test_seal_reproduction_bundle_rebuilds_real_retained_parquet_and_evidence(
    tmp_path: Path,
) -> None:
    store, record = _record(tmp_path)
    dataset = ParquetMaterializer(store, tmp_path / "parquet").materialize((record,))
    manifest = json.loads((dataset.directory / "dataset-manifest.json").read_bytes())
    runtime_lock = tmp_path / "runtime.lock"
    runtime_lock.write_text("frozen local wheel inventory", encoding="utf-8")
    queries_path = tmp_path / "queries.json"
    grader_path = tmp_path / "grader-result.json"
    evidence_path = tmp_path / "normalized-evidence.json"
    queries_path.write_bytes(
        canonical_bytes(
            [
                {
                    "name": "eligible",
                    "table": "eligible_outcomes",
                    "columns": ["endpoint_id", "numeric_value"],
                    "equals": [],
                    "numeric_column": "numeric_value",
                    "figure_columns": ["endpoint_id", "numeric_value"],
                }
            ]
        )
    )
    grader_path.write_bytes(
        canonical_bytes(
            {
                "binary": {"passed": True},
                "continuous": {"score": 1.0},
                "tests": {"hidden": True, "native": True},
            }
        )
    )
    evidence_path.write_bytes(
        canonical_bytes(
            {
                "created_at": "2026-08-11T00:00:00Z",
                "workspace_path": "workspace\\result.json",
                "value": "retained",
            }
        )
    )
    bundle_root = tmp_path / "bundle"
    assert (
        main(
            [
                "seal-reproduction-bundle",
                "--parquet-root",
                str(dataset.directory.parent),
                "--dataset-version",
                dataset.dataset_version,
                "--queries",
                str(queries_path),
                "--grader-result",
                str(grader_path),
                "--normalized-evidence",
                str(evidence_path),
                "--protocol-sha256",
                manifest["protocol_sha256"][0],
                "--runtime-lock",
                str(runtime_lock),
                "--output-root",
                str(bundle_root),
            ]
        )
        == 0
    )
    bundle = ReproductionBundle.parse((bundle_root / "reproduction-bundle.json").read_bytes())

    rebuilt = ReplayOutputs.from_document(
        rebuild_retained_outputs(bundle.bytes(), {"cas": str(bundle_root)})
    )
    comparison = compare_replay_outputs(bundle, rebuilt)

    assert comparison.matches is True
    assert bundle.protocol_sha256 == manifest["protocol_sha256"][0]
    assert (bundle_root / "reproduction-bundle.json").is_file()
