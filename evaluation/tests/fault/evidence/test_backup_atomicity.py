from __future__ import annotations

import errno
import shutil
import time
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path

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
from memrelay_eval.evidence import backup as backup_module
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


def _hold_production_backup_lock(lock_path: str, ready: object) -> None:
    """Hold the exact production lease until the parent terminates this process."""

    with backup_module._BackupLock(Path(lock_path)):
        ready.put("locked")  # type: ignore[union-attr]
        time.sleep(60)


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
    assert error.value.code in {
        "backup_generation_incomplete",
        "backup_stale_or_tampered_receipt",
    }
    ledger.close()


def test_backup_lock_fails_closed_on_noncontention_acquisition_error(tmp_path, monkeypatch) -> None:
    lock_path = tmp_path / ".memrelay-backup.lock"
    original_open = Path.open

    def denied_open(path: Path, *args: object, **kwargs: object):
        if path == lock_path:
            raise OSError(errno.EPERM, "simulated permission loss")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)
    with pytest.raises(BackupConformanceError) as error, backup_module._BackupLock(lock_path):
        raise AssertionError("lock acquisition unexpectedly succeeded")

    assert error.value.code == "backup_lock_acquisition_failed"


def test_process_death_releases_production_lock_without_touching_prior_generation(tmp_path) -> None:
    service, ledger, run, attempt, backup = _service(tmp_path)
    prior = service.backup_terminal_run(run_id=run, attempt_id=attempt)
    prior_receipt = (
        backup / "generations" / prior.generation_id / "backup-receipt.json"
    ).read_bytes()
    context = get_context("spawn")
    ready = context.Queue()
    process = context.Process(
        target=_hold_production_backup_lock,
        args=(str(backup / ".memrelay-backup.lock"), ready),
    )
    process.start()
    assert ready.get(timeout=15) == "locked"
    try:
        with pytest.raises(BackupConformanceError) as blocked:
            service.backup_terminal_run(run_id=run, attempt_id=attempt)
        assert blocked.value.code == "backup_concurrent_writer_detected"
    finally:
        process.terminate()
        process.join(timeout=15)
    assert process.exitcode is not None

    retry = service.backup_terminal_run(run_id=run, attempt_id=attempt)

    assert retry.generation_id != prior.generation_id
    assert (
        backup / "generations" / prior.generation_id / "backup-receipt.json"
    ).read_bytes() == prior_receipt
    ledger.close()


def test_publish_collision_accepts_only_an_identical_completed_generation(
    tmp_path, monkeypatch
) -> None:
    service, ledger, run, attempt, backup = _service(tmp_path)
    receipt = service.backup_terminal_run(run_id=run, attempt_id=attempt)
    destination = backup / "generations" / receipt.generation_id
    staging = tmp_path / "racing-generation"
    shutil.copytree(destination, staging)
    shutil.rmtree(destination)

    def competing_publish(source: Path, target: Path) -> None:
        assert source == staging
        assert target == destination
        shutil.copytree(source, target)
        raise PermissionError(errno.EACCES, "simulated Windows directory collision")

    monkeypatch.setattr(backup_module.os, "replace", competing_publish)
    service._publish_or_verify_generation(staging, receipt)

    assert (destination / "backup-receipt.json").read_bytes() == receipt.bytes()
    shutil.rmtree(staging)
    ledger.close()


def test_publish_collision_rejects_an_incomplete_competitor_generation(
    tmp_path, monkeypatch
) -> None:
    service, ledger, run, attempt, backup = _service(tmp_path)
    receipt = service.backup_terminal_run(run_id=run, attempt_id=attempt)
    destination = backup / "generations" / receipt.generation_id
    staging = tmp_path / "racing-generation"
    shutil.copytree(destination, staging)
    shutil.rmtree(destination)

    def incomplete_competitor(source: Path, target: Path) -> None:
        assert source == staging
        target.mkdir(parents=True)
        (target / "backup-receipt.json").write_bytes(b"partial")
        raise PermissionError(errno.EACCES, "simulated Windows directory collision")

    monkeypatch.setattr(backup_module.os, "replace", incomplete_competitor)
    with pytest.raises(BackupConformanceError) as error:
        service._publish_or_verify_generation(staging, receipt)

    assert error.value.code == "backup_stale_or_tampered_receipt"
    shutil.rmtree(staging)
    ledger.close()
