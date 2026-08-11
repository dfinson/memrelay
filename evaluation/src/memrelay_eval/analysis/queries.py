"""Read only the sealed Story 5.1 analysis boundary for frozen ITT construction."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pyarrow.parquet as pq

from memrelay_eval.domain.errors import AnalysisError

MaterializedTables = tuple[
    Mapping[str, object],
    list[dict[str, object]],
    list[dict[str, object]],
]


@dataclass(frozen=True, slots=True)
class MaterializedAnalysisDataset:
    """Verified Story 5.1 rows and their immutable dataset-manifest identity."""

    dataset_manifest_sha256: str
    assigned_units: list[dict[str, object]]
    eligible_outcomes: list[dict[str, object]]


def read_materialized_dataset(dataset_directory: Path | str) -> MaterializedAnalysisDataset:
    """Load the two immutable Parquet inputs after checking their published manifest."""
    directory = Path(dataset_directory)
    manifest_path = directory / "dataset-manifest.json"
    derivation_path = directory / "derivation-manifest.json"
    assigned_path = directory / "assigned_units.parquet"
    outcomes_path = directory / "eligible_outcomes.parquet"
    if not all(
        path.is_file() for path in (manifest_path, derivation_path, assigned_path, outcomes_path)
    ):
        raise AnalysisError("analysis_dataset_incomplete")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("analysis_dataset_manifest_invalid") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("artifact_type") != "parquet_dataset_manifest"
    ):
        raise AnalysisError("analysis_dataset_manifest_invalid")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise AnalysisError("analysis_dataset_manifest_invalid")
    _verify_table_file(assigned_path, files.get("assigned_units"))
    _verify_table_file(outcomes_path, files.get("eligible_outcomes"))
    try:
        derivation = json.loads(derivation_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("analysis_derivation_manifest_invalid") from error
    manifest_sha256 = sha256(manifest_path.read_bytes()).hexdigest()
    if (
        not isinstance(derivation, dict)
        or derivation.get("artifact_type") != "parquet_derivation_manifest"
        or derivation.get("dataset_manifest_sha256") != manifest_sha256
    ):
        raise AnalysisError("analysis_lineage_incomplete")
    return MaterializedAnalysisDataset(
        dataset_manifest_sha256=manifest_sha256,
        assigned_units=pq.read_table(assigned_path).to_pylist(),
        eligible_outcomes=pq.read_table(outcomes_path).to_pylist(),
    )


def read_materialized_tables(dataset_directory: Path | str) -> MaterializedTables:
    """Return verified materialized rows for callers that do not need the identity wrapper."""
    dataset = read_materialized_dataset(dataset_directory)
    return (
        {"dataset_manifest_sha256": dataset.dataset_manifest_sha256},
        dataset.assigned_units,
        dataset.eligible_outcomes,
    )


def _verify_table_file(path: Path, reference: object) -> None:
    if not isinstance(reference, dict) or not isinstance(reference.get("sha256"), str):
        raise AnalysisError("analysis_dataset_manifest_invalid")
    if sha256(path.read_bytes()).hexdigest() != reference["sha256"]:
        raise AnalysisError("analysis_dataset_file_hash_mismatch")
