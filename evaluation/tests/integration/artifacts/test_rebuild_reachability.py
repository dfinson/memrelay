from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from memrelay_eval.adapters.artifacts.filesystem import FilesystemArtifactStore
from memrelay_eval.domain.entities import ArtifactManifest, ArtifactRef
from memrelay_eval.domain.errors import ArtifactIntegrityError
from memrelay_eval.domain.ids import AttemptId, RetentionPolicyId, RunId
from memrelay_eval.domain.states import ArtifactScope
from memrelay_eval.evidence.manifest import manifest_bytes


def _manifest(
    reference: ArtifactRef,
    *,
    run_id: RunId,
    attempt_id: AttemptId,
    source_ids: tuple = (),
) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=reference.artifact_id,
        kind="integration_fixture",
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        media_type="application/octet-stream",
        created_at=datetime(2026, 8, 5, tzinfo=UTC),
        producer_component="integration_test",
        producer_version="1.0.0",
        classification="synthetic",
        contains_secrets=False,
        source_artifact_ids=source_ids,
        retention_policy_id=RetentionPolicyId.new(),
        encryption=None,
        scope=ArtifactScope.ATTEMPT,
        run_id=run_id,
        attempt_id=attempt_id,
    )


def test_rebuild_reproduces_reachability_and_qualification(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    run_id, attempt_id = RunId.new(), AttemptId.new()
    source = store.put_bytes(b"source", media_type="text/plain", classification="synthetic")
    store.write_manifest(_manifest(source, run_id=run_id, attempt_id=attempt_id))
    derived = store.put_bytes(b"derived", media_type="text/plain", classification="synthetic")
    store.write_manifest(
        _manifest(
            derived,
            run_id=run_id,
            attempt_id=attempt_id,
            source_ids=(source.artifact_id,),
        )
    )

    first = store.rebuild_reachability()
    later = store.put_bytes(b"later", media_type="text/plain", classification="synthetic")
    store.write_manifest(_manifest(later, run_id=run_id, attempt_id=attempt_id))
    second = store.rebuild_reachability()
    qualification = store.qualify()

    assert first.to_dict() != second.to_dict()
    assert first.runs[str(run_id)] == tuple(sorted((source.sha256, derived.sha256)))
    assert second.runs[str(run_id)] == tuple(sorted((source.sha256, derived.sha256, later.sha256)))
    assert first.evidence[derived.sha256] == (source.sha256,)
    assert qualification.qualified is True
    assert qualification.provenance == "durable_filesystem_cas"


def test_rebuild_properties_are_deterministic_for_random_binary_artifacts(tmp_path) -> None:
    generator = random.Random(4101)
    store = FilesystemArtifactStore(tmp_path)
    run_id, attempt_id = RunId.new(), AttemptId.new()
    expected: list[str] = []
    for length in range(33):
        data = bytes(generator.getrandbits(8) for _ in range(length))
        artifact = store.put_bytes(
            data, media_type="application/octet-stream", classification="synthetic"
        )
        store.write_manifest(_manifest(artifact, run_id=run_id, attempt_id=attempt_id))
        expected.append(artifact.sha256)

    assert store.rebuild_reachability().runs[str(run_id)] == tuple(sorted(expected))


def test_unresolved_sources_cannot_be_linked_or_rebuilt(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    artifact = store.put_bytes(b"derived", media_type="text/plain", classification="synthetic")
    missing = ArtifactRef.from_bytes(b"missing")
    with pytest.raises(ArtifactIntegrityError):
        store.write_manifest(
            _manifest(
                artifact,
                run_id=RunId.new(),
                attempt_id=AttemptId.new(),
                source_ids=(missing.artifact_id,),
            )
        )

    index = store.rebuild_reachability()
    qualification = store.qualify()
    assert index.orphaned_blobs == (artifact.sha256,)
    assert qualification.qualified is False


def test_rebuild_rejects_tampered_cyclic_manifest_sources_and_disqualifies(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    run_id, attempt_id = RunId.new(), AttemptId.new()
    first = store.put_bytes(b"first", media_type="text/plain", classification="synthetic")
    second = store.put_bytes(b"second", media_type="text/plain", classification="synthetic")
    first_manifest = _manifest(first, run_id=run_id, attempt_id=attempt_id)
    second_manifest = _manifest(second, run_id=run_id, attempt_id=attempt_id)
    store.write_manifest(first_manifest)
    store.write_manifest(second_manifest)

    cyclic_first = replace(first_manifest, source_artifact_ids=(second.artifact_id,))
    cyclic_second = replace(second_manifest, source_artifact_ids=(first.artifact_id,))
    for manifest in (cyclic_first, cyclic_second):
        payload = manifest_bytes(manifest)
        store.put_bytes(payload, media_type="application/json", classification="synthetic")
        manifest_path = tmp_path / "manifests" / "sha256" / manifest.sha256[:2]
        (manifest_path / f"{manifest.sha256[2:]}.json").write_bytes(payload)

    with pytest.raises(ArtifactIntegrityError, match="cycle"):
        store.rebuild_reachability()

    qualification = store.qualify()
    corruption_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "corruption").glob("*.json")
    ]
    assert qualification.qualified is False
    assert qualification.failure_reason == "evidence_integrity_failure"
    assert any(record["reason"] == "cyclic_source_reference" for record in corruption_records)
