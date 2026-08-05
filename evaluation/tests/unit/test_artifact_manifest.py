from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from memrelay_eval.domain.entities import ArtifactLink, ArtifactManifest, ArtifactRef
from memrelay_eval.domain.errors import InvalidArtifactManifestError
from memrelay_eval.domain.ids import AttemptId, ExperimentId, RetentionPolicyId, RunId
from memrelay_eval.domain.states import ArtifactScope


def manifest(scope: ArtifactScope, *, attempt_id: AttemptId | None = None) -> ArtifactManifest:
    data = b"fixture artifact"
    reference = ArtifactRef.from_bytes(data)
    return ArtifactManifest(
        artifact_id=reference.artifact_id,
        kind="fixture",
        sha256=reference.sha256,
        size_bytes=len(data),
        media_type="application/octet-stream",
        created_at=datetime(2026, 8, 5, tzinfo=UTC),
        producer_component="unit_test",
        producer_version="1.0.0",
        classification="synthetic",
        contains_secrets=False,
        source_artifact_ids=(),
        retention_policy_id=RetentionPolicyId.new(),
        encryption=None,
        scope=scope,
        experiment_id=ExperimentId.new() if scope is ArtifactScope.EXPERIMENT else None,
        run_id=RunId.new() if scope is not ArtifactScope.EXPERIMENT else None,
        attempt_id=attempt_id,
    )


def test_manifest_supports_experiment_run_and_attempt_scopes() -> None:
    experiment_manifest = manifest(ArtifactScope.EXPERIMENT)
    run_manifest = manifest(ArtifactScope.RUN)
    attempt_manifest = manifest(ArtifactScope.ATTEMPT, attempt_id=AttemptId.new())

    assert experiment_manifest.to_dict()["attempt_id"] is None
    assert run_manifest.to_dict()["attempt_id"] is None
    assert attempt_manifest.to_dict()["attempt_id"] == str(attempt_manifest.attempt_id)


@pytest.mark.parametrize("scope", (ArtifactScope.EXPERIMENT, ArtifactScope.RUN))
def test_pre_attempt_artifacts_reject_fabricated_attempt_ids(scope: ArtifactScope) -> None:
    with pytest.raises(InvalidArtifactManifestError):
        manifest(scope, attempt_id=AttemptId.new())


def test_attempt_artifacts_require_attempt_id() -> None:
    with pytest.raises(InvalidArtifactManifestError):
        manifest(ArtifactScope.ATTEMPT)


def test_artifact_link_owns_pre_attempt_evidence_without_attempt_id() -> None:
    reference = ArtifactRef.from_bytes(b"effective configuration")
    link = ArtifactLink(reference, "effective_configuration", run_id=RunId.new())
    assert link.attempt_id is None


def test_manifest_rejects_wrong_schema_digest_and_timestamp() -> None:
    with pytest.raises(InvalidArtifactManifestError):
        ArtifactManifest(
            artifact_id=ArtifactRef.from_bytes(b"data").artifact_id,
            kind="fixture",
            sha256=sha256(b"other").hexdigest(),
            size_bytes=4,
            media_type="application/octet-stream",
            created_at=datetime(2026, 8, 5),
            producer_component="test",
            producer_version="1",
            classification="synthetic",
            contains_secrets=False,
            source_artifact_ids=(),
            retention_policy_id=RetentionPolicyId.new(),
            encryption=None,
            scope=ArtifactScope.RUN,
            run_id=RunId.new(),
            schema_version="2.0.0",
        )
