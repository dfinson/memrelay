from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from memrelay_eval.adapters.artifacts.filesystem import FilesystemArtifactStore
from memrelay_eval.domain.entities import ArtifactManifest, ArtifactRef
from memrelay_eval.domain.errors import (
    ArtifactAuthorityConflictError,
    ArtifactIntegrityError,
    ArtifactRetentionError,
)
from memrelay_eval.domain.ids import AttemptId, RetentionPolicyId, RunId
from memrelay_eval.domain.states import ArtifactScope


def _manifest(reference: ArtifactRef, *, kind: str = "fault_fixture") -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=reference.artifact_id,
        kind=kind,
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        media_type="application/octet-stream",
        created_at=datetime(2026, 8, 5, tzinfo=UTC),
        producer_component="fault_test",
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


@pytest.mark.parametrize("boundary", ("blob_staged", "blob_published"))
def test_interrupted_publication_is_retryable_without_overwrite(tmp_path, boundary: str) -> None:
    def interrupt(stage: str) -> None:
        if stage == boundary:
            raise RuntimeError("simulated interruption")

    interrupted = FilesystemArtifactStore(tmp_path, publication_hook=interrupt)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        interrupted.put_bytes(b"retryable", media_type="text/plain", classification="synthetic")
    assert not list(tmp_path.rglob(".*.staging"))

    recovered = FilesystemArtifactStore(tmp_path)
    artifact = recovered.put_bytes(
        b"retryable", media_type="text/plain", classification="synthetic"
    )
    assert recovered.open_verified(artifact) == b"retryable"
    recovered.write_manifest(_manifest(artifact))
    assert recovered.rebuild_reachability().attempts


@pytest.mark.parametrize("boundary", ("manifest_staged", "manifest_published"))
def test_interrupted_manifest_publication_recovers_idempotently(tmp_path, boundary: str) -> None:
    initial = FilesystemArtifactStore(tmp_path)
    artifact = initial.put_bytes(
        b"manifest retry", media_type="text/plain", classification="synthetic"
    )
    manifest = _manifest(artifact)

    def interrupt(stage: str) -> None:
        if stage == boundary:
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        FilesystemArtifactStore(tmp_path, publication_hook=interrupt).write_manifest(manifest)

    recovered = FilesystemArtifactStore(tmp_path)
    with pytest.raises(ArtifactIntegrityError):
        recovered.read_manifest(artifact)
    recovered.write_manifest(manifest)
    assert recovered.read_manifest(artifact).sha256 == artifact.sha256
    assert recovered.rebuild_reachability().attempts


def test_manifest_conflict_preserves_evidence_and_blocks_authority(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    artifact = store.put_bytes(b"authority", media_type="text/plain", classification="synthetic")
    store.write_manifest(_manifest(artifact, kind="first"))

    with pytest.raises(ArtifactAuthorityConflictError):
        store.write_manifest(_manifest(artifact, kind="conflicting"))

    assert store.read_manifest(artifact).kind == "first"
    assert list((tmp_path / "corruption").glob("*.json"))


def test_concurrent_manifest_conflict_leaves_no_orphan_blob(tmp_path) -> None:
    winner = FilesystemArtifactStore(tmp_path)
    artifact = winner.put_bytes(
        b"racing authority", media_type="text/plain", classification="synthetic"
    )
    first = _manifest(artifact, kind="first")
    second = _manifest(artifact, kind="second")
    contender = FilesystemArtifactStore(tmp_path)
    has_interleaved = False

    def interleave(stage: str) -> None:
        nonlocal has_interleaved
        if stage == "manifest_staged" and not has_interleaved:
            has_interleaved = True
            contender.write_manifest(second)

    interrupted = FilesystemArtifactStore(tmp_path, publication_hook=interleave)
    with pytest.raises(ArtifactAuthorityConflictError):
        interrupted.write_manifest(first)

    assert contender.read_manifest(artifact).kind == "second"
    assert contender.rebuild_reachability().attempts


def test_concurrent_duplicate_puts_are_idempotent(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                store.put_bytes,
                b"concurrent duplicate",
                media_type="text/plain",
                classification="synthetic",
            )
            for _ in range(2)
        ]
        references = tuple(future.result() for future in futures)

    assert references[0] == references[1]
    assert store.open_verified(references[0]) == b"concurrent duplicate"


def test_short_staging_writes_are_completed_before_publication(tmp_path, monkeypatch) -> None:
    original_open = Path.open

    class PartialWriter:
        def __init__(self, handle) -> None:
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args) -> None:
            self.handle.__exit__(*args)

        def __getattr__(self, name: str):
            return getattr(self.handle, name)

        def write(self, data) -> int:
            return self.handle.write(data[: max(1, len(data) // 2)])

    def partial_open(path: Path, mode: str = "r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return PartialWriter(handle) if mode == "xb" else handle

    monkeypatch.setattr(Path, "open", partial_open)
    store = FilesystemArtifactStore(tmp_path)
    data = b"short writes must not truncate immutable evidence"
    artifact = store.put_bytes(data, media_type="text/plain", classification="synthetic")

    assert store.open_verified(artifact) == data


def test_existing_collision_and_corrupt_copy_fail_closed(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    artifact = store.put_bytes(b"trusted", media_type="text/plain", classification="synthetic")
    blob_path = tmp_path / "blobs" / "sha256" / artifact.sha256[:2] / artifact.sha256[2:]
    blob_path.write_bytes(b"collision")

    with pytest.raises(ArtifactIntegrityError):
        store.put_bytes(b"trusted", media_type="text/plain", classification="synthetic")

    clean = FilesystemArtifactStore(tmp_path / "clean")
    clean_artifact = clean.put_bytes(
        b"copy source", media_type="text/plain", classification="synthetic"
    )
    destination = tmp_path / "copy.bin"
    destination.write_bytes(b"different")
    with pytest.raises(ArtifactAuthorityConflictError):
        clean.copy_verified(clean_artifact, destination)


def test_retention_never_deletes_unretired_evidence(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    artifact = store.put_bytes(b"retained", media_type="text/plain", classification="synthetic")

    with pytest.raises(ArtifactRetentionError):
        store.delete(artifact)


def test_leaked_staging_file_is_not_authority_or_an_orphan(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    artifact = store.put_bytes(
        b"authoritative", media_type="text/plain", classification="synthetic"
    )
    store.write_manifest(_manifest(artifact))
    staging = tmp_path / "blobs" / "sha256" / artifact.sha256[:2] / ".leaked.staging"
    staging.write_bytes(b"uncommitted")

    index = store.rebuild_reachability()
    qualification = store.qualify()

    assert index.orphaned_blobs == ()
    assert qualification.qualified is True
    assert store.open_verified(artifact) == b"authoritative"
