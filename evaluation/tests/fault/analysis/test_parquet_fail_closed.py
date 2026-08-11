from __future__ import annotations

from dataclasses import replace

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
