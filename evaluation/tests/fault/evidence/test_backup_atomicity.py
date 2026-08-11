from __future__ import annotations

from datetime import UTC, datetime

import pytest
from memrelay_eval.adapters.artifacts.filesystem import FilesystemArtifactStore
from memrelay_eval.domain.entities import ArtifactLink, ArtifactManifest
from memrelay_eval.domain.errors import BackupConformanceError
from memrelay_eval.domain.ids import (
    AssignmentId,
    AttemptId,
    ExperimentId,
    IntentId,
    ProtocolId,
    RetentionPolicyId,
    RunId,
)
from memrelay_eval.domain.intents import (
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    IntentMetadata,
)
from memrelay_eval.domain.states import ArtifactScope
from memrelay_eval.evidence.backup import TerminalEvidenceBackup
from memrelay_eval.ledger import SqliteLedger


def _seed(ledger: SqliteLedger) -> tuple[RunId, AttemptId]:
    now = datetime(2026, 8, 11, tzinfo=UTC)

    def metadata() -> IntentMetadata:
        return IntentMetadata(IntentId.new(), now, reason_code="backup_fixture")

    experiment, run, attempt = ExperimentId.new(), RunId.new(), AttemptId.new()
    ledger.submit_intent(CreateExperimentIntent(metadata(), experiment, ProtocolId.new()))
    ledger.submit_intent(CreateRunIntent(metadata(), run, experiment, AssignmentId.new()))
    ledger.submit_intent(CreateAttemptIntent(metadata(), attempt, run))
    return run, attempt


def _service(tmp_path, *, link_failure: bool = False):
    artifacts = tmp_path / "artifacts"
    backup = tmp_path / "backup"
    backup.mkdir()
    store = FilesystemArtifactStore(artifacts)
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed(ledger)
    artifact = store.put_bytes(
        b"terminal inspect json", media_type="application/json", classification="synthetic"
    )
    store.write_manifest(
        ArtifactManifest(
            artifact_id=artifact.artifact_id,
            kind="inspect_json",
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            media_type="application/json",
            created_at=datetime(2026, 8, 11, tzinfo=UTC),
            producer_component="test",
            producer_version="1.0.0",
            classification="synthetic",
            contains_secrets=False,
            source_artifact_ids=(),
            retention_policy_id=RetentionPolicyId.new(),
            encryption=None,
            scope=ArtifactScope.ATTEMPT,
            run_id=run,
            attempt_id=attempt,
        )
    )
    ledger.append_artifact_link(
        ArtifactLink(artifact, "inspect_export", run_id=run, attempt_id=attempt)
    )

    class LinkFailingBackup(TerminalEvidenceBackup):
        def _link_receipt(self, *args):  # type: ignore[no-untyped-def]
            if link_failure:
                raise BackupConformanceError("backup_receipt_link_failed")
            return super()._link_receipt(*args)

    service = LinkFailingBackup(
        artifacts_root=artifacts,
        ledger=ledger,
        ledger_path=tmp_path / "ledger.sqlite",
        backup_root=backup,
        volume_identity=lambda path: "source-volume" if path == artifacts else "backup-volume",
    )
    return service, ledger, run, attempt, backup


def test_partial_generation_never_becomes_a_valid_receipt(tmp_path) -> None:
    service, ledger, run, attempt, backup = _service(tmp_path, link_failure=True)
    with pytest.raises(BackupConformanceError, match="backup or restore"):
        service.backup_terminal_run(run_id=run, attempt_id=attempt)

    generation = next((backup / "generations").iterdir())
    next(
        path
        for path in generation.rglob("*")
        if path.is_file() and path.name != "backup-receipt.json"
    ).unlink()
    with pytest.raises(BackupConformanceError) as error:
        service.backup_terminal_run(run_id=run, attempt_id=attempt)
    assert error.value.code in {"backup_generation_incomplete", "backup_stale_or_tampered_receipt"}
    ledger.close()
