from __future__ import annotations

import errno
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import memrelay_eval.evidence.parquet as parquet_module
import pyarrow.parquet as pq
import pytest
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import ArtifactIntegrityError, MaterializationError
from memrelay_eval.evidence.parquet import ParquetMaterializer
from tests.integration.analysis.test_parquet_materialization import _record


def test_duplicate_assignment_identity_is_rejected(tmp_path) -> None:
    store, record = _record(tmp_path)

    with pytest.raises(MaterializationError) as error:
        ParquetMaterializer(store, tmp_path / "parquet").materialize((record, record))

    assert error.value.code == "duplicate_terminal_identity"


def test_tampered_source_artifact_prevents_publication(tmp_path) -> None:
    store, record = _record(tmp_path)
    source = next(item.artifact for item in record.request.evidence if item.artifact is not None)
    assert source is not None
    blob = store.root / "blobs" / "sha256" / source.sha256[:2] / source.sha256[2:]
    blob.write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError):
        ParquetMaterializer(store, tmp_path / "parquet").materialize((record,))

    assert not list((tmp_path / "parquet").glob("parquet-v*"))


def test_outcome_authority_must_bind_the_confirmatory_value(tmp_path) -> None:
    store, record = _record(tmp_path)
    unbound = ArtifactRef.from_bytes(b"unbound-outcome-evidence")
    outcome = record.eligible_outcomes[0]
    forged = replace(
        record,
        eligible_outcomes=(replace(outcome, evidence_refs=(unbound,)),),
    )

    with pytest.raises(MaterializationError) as error:
        ParquetMaterializer(store, tmp_path / "parquet").materialize((forged,))

    assert error.value.code == "eligible_outcome_authority_conflict"


def test_partial_or_schema_drifted_publication_is_rejected(tmp_path) -> None:
    store, record = _record(tmp_path)
    materializer = ParquetMaterializer(store, tmp_path / "parquet")
    result = materializer.materialize((record,))
    outcomes = result.directory / "eligible_outcomes.parquet"
    outcomes.unlink()

    with pytest.raises(MaterializationError) as partial:
        materializer.materialize((record,))
    assert partial.value.code == "published_dataset_partial"

    result = ParquetMaterializer(store, tmp_path / "parquet-two").materialize((record,))
    assigned = result.directory / "assigned_units.parquet"
    table = pq.read_table(assigned).replace_schema_metadata({b"schema": b"drift"})
    pq.write_table(table, assigned)

    with pytest.raises(MaterializationError) as drift:
        materializer._verify_published(result)
    assert drift.value.code == "parquet_schema_drift"


def test_concurrent_publishers_reuse_one_verified_immutable_version(tmp_path) -> None:
    store, record = _record(tmp_path)
    root = tmp_path / "parquet"

    def materialize_once(_: int):
        return ParquetMaterializer(store, root).materialize((record,))

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(materialize_once, range(12)))

    assert {result.dataset_version for result in results} == {results[0].dataset_version}
    assert {result.assigned_units_ref for result in results} == {results[0].assigned_units_ref}
    assert {result.eligible_outcomes_ref for result in results} == {
        results[0].eligible_outcomes_ref
    }
    assert list(root.glob("parquet-v*")) == [results[0].directory]


def test_corrupt_destination_collision_fails_closed(tmp_path, monkeypatch) -> None:
    store, record = _record(tmp_path)
    root = tmp_path / "parquet"
    real_replace = parquet_module.os.replace

    def destination_collision(source: Path | str, destination: Path | str) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path.name.endswith(".staging") and destination_path.name.startswith("parquet-v"):
            destination_path.mkdir()
            raise PermissionError(errno.EACCES, "destination access denied", str(destination_path))
        real_replace(source, destination)

    monkeypatch.setattr(parquet_module.os, "replace", destination_collision)

    with pytest.raises(MaterializationError) as error:
        ParquetMaterializer(store, root).materialize((record,))

    assert error.value.code == "published_manifest_missing_or_invalid"
