from __future__ import annotations

import pytest
from memrelay_eval.domain.errors import BackupConformanceError
from memrelay_eval.evidence.backup import (
    BackupConformanceGate,
    RestorePolicy,
    restore_drill,
)
from tests.fault.evidence.test_backup_atomicity import _service


def test_restore_drill_rebuilds_verified_reachability_in_quarantine(tmp_path) -> None:
    service, ledger, run, attempt, backup = _service(tmp_path)
    receipt = service.backup_terminal_run(run_id=run, attempt_id=attempt)
    gate = BackupConformanceGate(backup)

    with pytest.raises(BackupConformanceError, match="backup or restore"):
        gate.require_paid_pilot_admission()

    report = restore_drill(
        backup_root=backup,
        generation_id=receipt.generation_id,
        quarantine_root=tmp_path / "quarantine",
        policy=RestorePolicy("baseline-synthetic", lambda _: True),
    )

    assert report.inventory_sha256 == receipt.inventory_sha256
    assert report.verified_ledger_link_count == 1
    assert (tmp_path / "quarantine" / "restore-report.json").is_file()
    gate.require_paid_pilot_admission()
    ledger.close()


def test_restore_rejects_tampered_generation_before_any_render_or_index(tmp_path) -> None:
    service, ledger, run, attempt, backup = _service(tmp_path)
    receipt = service.backup_terminal_run(run_id=run, attempt_id=attempt)
    generation = backup / "generations" / receipt.generation_id
    item = next(item for item in receipt.items if str(item["path"]).startswith("artifacts/blobs"))
    (generation / str(item["path"])).write_bytes(b"tampered")

    with pytest.raises(BackupConformanceError) as error:
        restore_drill(
            backup_root=backup,
            generation_id=receipt.generation_id,
            quarantine_root=tmp_path / "quarantine",
            policy=RestorePolicy("baseline-synthetic", lambda _: True),
        )
    assert error.value.code in {"backup_generation_hash_mismatch", "backup_inventory_hash_mismatch"}
    assert not (tmp_path / "quarantine").exists()
    ledger.close()


def test_restore_applies_current_revocation_policy_before_index_rebuild(tmp_path) -> None:
    service, ledger, run, attempt, backup = _service(tmp_path)
    receipt = service.backup_terminal_run(run_id=run, attempt_id=attempt)

    with pytest.raises(BackupConformanceError) as error:
        restore_drill(
            backup_root=backup,
            generation_id=receipt.generation_id,
            quarantine_root=tmp_path / "quarantine",
            policy=RestorePolicy("revoked-record", lambda _: False),
        )

    assert error.value.code == "restore_policy_rejected_artifact"
    assert not (tmp_path / "quarantine").exists()
    ledger.close()
