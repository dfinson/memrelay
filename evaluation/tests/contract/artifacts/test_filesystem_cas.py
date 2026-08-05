from __future__ import annotations

from datetime import UTC, datetime

import pytest

from memrelay_eval.adapters.artifacts.filesystem import FilesystemArtifactStore
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.domain.entities import ArtifactManifest, ArtifactRef
from memrelay_eval.domain.errors import ArtifactIntegrityError, InvalidArtifactManifestError
from memrelay_eval.domain.ids import AttemptId, RetentionPolicyId, RunId
from memrelay_eval.domain.states import ArtifactScope
from memrelay_eval.evidence.manifest import canonical_json_bytes, manifest_bytes, parse_manifest


def _manifest(reference: ArtifactRef) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=reference.artifact_id,
        kind="contract_fixture",
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        media_type="application/octet-stream",
        created_at=datetime(2026, 8, 5, tzinfo=UTC),
        producer_component="contract_test",
        producer_version="1.0.0",
        classification="synthetic",
        contains_secrets=False,
        source_artifact_ids=(),
        retention_policy_id=RetentionPolicyId.new(),
        encryption=None,
        scope=ArtifactScope.ATTEMPT,
        run_id=RunId.new(),
        attempt_id=AttemptId.new(),
    )


@pytest.mark.parametrize("store_factory", (InMemoryArtifactStore, FilesystemArtifactStore))
def test_artifact_store_port_put_get_and_manifest(
    tmp_path, store_factory: type[InMemoryArtifactStore] | type[FilesystemArtifactStore]
) -> None:
    store = store_factory() if store_factory is InMemoryArtifactStore else store_factory(tmp_path)
    first = store.put_bytes(b"contract bytes", media_type="text/plain", classification="synthetic")
    second = store.put_bytes(b"contract bytes", media_type="text/plain", classification="synthetic")
    manifest = _manifest(first)

    assert first == second
    assert store.open_verified(first) == b"contract bytes"
    assert store.write_manifest(manifest).sha256 == ArtifactRef.from_bytes(manifest_bytes(manifest)).sha256


def test_filesystem_store_uses_digest_path_and_strict_manifest_bytes(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    artifact = store.put_bytes(b"path stable", media_type="text/plain", classification="synthetic")
    manifest = _manifest(artifact)
    store.write_manifest(manifest)

    assert (tmp_path / "blobs" / "sha256" / artifact.sha256[:2] / artifact.sha256[2:]).read_bytes() == (
        b"path stable"
    )
    assert parse_manifest(manifest_bytes(manifest)) == manifest
    with pytest.raises(InvalidArtifactManifestError):
        parse_manifest(b'{"schema_version":"1.0.0"}\n')
    tampered = manifest.to_dict()
    tampered["unexpected"] = "forbidden"
    with pytest.raises(InvalidArtifactManifestError):
        parse_manifest(canonical_json_bytes(tampered))


def test_canonical_json_orders_non_bmp_keys_by_utf16_code_units() -> None:
    canonical = canonical_json_bytes({"\ue000": "bmp", "\U00010000": "non_bmp"}).decode("utf-8")
    assert canonical.startswith('{"\U00010000"')


def test_filesystem_store_fails_closed_for_tampered_blob(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    artifact = store.put_bytes(b"untampered", media_type="text/plain", classification="synthetic")
    path = tmp_path / "blobs" / "sha256" / artifact.sha256[:2] / artifact.sha256[2:]
    path.write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError):
        store.open_verified(artifact)

    assert list((tmp_path / "corruption").glob("*.json"))
