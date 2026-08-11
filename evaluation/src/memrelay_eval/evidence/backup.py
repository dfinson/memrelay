"""Independent-volume, append-only terminal-evidence backup and restore drills."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from errno import EACCES, EAGAIN, EEXIST, ENOTEMPTY
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from memrelay_eval.adapters.artifacts.filesystem import FilesystemArtifactStore
from memrelay_eval.domain.entities import ArtifactLink, ArtifactManifest, ArtifactRef
from memrelay_eval.domain.errors import BackupConformanceError, LedgerDirectWriteError
from memrelay_eval.domain.ids import AttemptId, RetentionPolicyId, RunId
from memrelay_eval.domain.states import ArtifactScope
from memrelay_eval.evidence.manifest import canonical_json_bytes
from memrelay_eval.ledger.repository import verify_snapshot_artifact_links, verify_snapshot_file

if os.name == "nt":
    import msvcrt
else:
    import fcntl

if TYPE_CHECKING:
    from memrelay_eval.domain.ports import LedgerPort


_RECEIPT_SCHEMA_VERSION = "1.0.0"
_RESTORE_SCHEMA_VERSION = "1.0.0"
_RTO_SECONDS = 24 * 60 * 60
_IGNORED_ARTIFACT_DIRECTORIES = frozenset({"indexes", "corruption"})


@dataclass(frozen=True, slots=True)
class BackupReceipt:
    """A verified, immutable generation receipt; it never authorizes source mutation."""

    generation_id: str
    inventory_sha256: str
    run_id: str
    attempt_id: str
    source_volume: str
    target_volume: str
    items: tuple[dict[str, object], ...]
    started_at: str
    completed_at: str
    highest_terminal_position: int
    rpo: str
    tool_version: str
    schema_version: str = _RECEIPT_SCHEMA_VERSION

    def to_document(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "completed_at": self.completed_at,
            "generation_id": self.generation_id,
            "highest_terminal_position": self.highest_terminal_position,
            "inventory_sha256": self.inventory_sha256,
            "items": list(self.items),
            "rpo": self.rpo,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "source_volume": self.source_volume,
            "started_at": self.started_at,
            "target_volume": self.target_volume,
            "tool_version": self.tool_version,
        }

    def bytes(self) -> bytes:
        return canonical_json_bytes(self.to_document())


@dataclass(frozen=True, slots=True)
class RestoreReport:
    """Evidence retained by a quarantined restore drill."""

    generation_id: str
    inventory_sha256: str
    restore_root: str
    elapsed_seconds: int
    verified_item_count: int
    verified_ledger_link_count: int
    policy_version: str
    completed_at: str
    schema_version: str = _RESTORE_SCHEMA_VERSION

    def to_document(self) -> dict[str, object]:
        return {
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
            "generation_id": self.generation_id,
            "inventory_sha256": self.inventory_sha256,
            "policy_version": self.policy_version,
            "restore_root": self.restore_root,
            "schema_version": self.schema_version,
            "verified_item_count": self.verified_item_count,
            "verified_ledger_link_count": self.verified_ledger_link_count,
        }


@dataclass(frozen=True, slots=True)
class RestorePolicy:
    """Current quarantine policy applied before restored objects are indexed or read."""

    version: str
    permits: Callable[[ArtifactManifest], bool]


class BackupConformanceGate:
    """Persists the categorical paid-pilot backup/restore admission state."""

    def __init__(self, backup_root: Path | str) -> None:
        self.path = Path(backup_root) / "conformance" / "backup-restore.json"

    def record_backup(self, receipt: BackupReceipt) -> None:
        self._write(
            {
                "generation_id": receipt.generation_id,
                "inventory_sha256": receipt.inventory_sha256,
                "reason_code": None,
                "status": "backup_verified_restore_required",
            }
        )

    def record_restore(self, report: RestoreReport) -> None:
        self._write(
            {
                "generation_id": report.generation_id,
                "inventory_sha256": report.inventory_sha256,
                "reason_code": None,
                "status": "qualified",
            }
        )

    def block(self, reason_code: str) -> None:
        self._write(
            {
                "generation_id": None,
                "inventory_sha256": None,
                "reason_code": reason_code,
                "status": "blocked",
            }
        )

    def require_paid_pilot_admission(self) -> None:
        try:
            document = json.loads(self.path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BackupConformanceError("backup_restore_unqualified") from error
        if canonical_json_bytes(document) != self.path.read_bytes():
            raise BackupConformanceError("backup_restore_gate_tampered")
        if document.get("status") != "qualified":
            raise BackupConformanceError(
                str(document.get("reason_code") or "backup_restore_unqualified")
            )

    def _write(self, document: dict[str, object]) -> None:
        payload = canonical_json_bytes(document)
        staging = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.staging")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _write_bytes(staging, payload)
            os.replace(staging, self.path)
            _fsync_directory(self.path.parent)
        finally:
            try:
                staging.unlink()
            except FileNotFoundError:
                pass


def canonical_volume_identity(path: Path | str) -> str:
    """Return a canonical local-volume identity, not a drive-letter spelling."""

    resolved = Path(path).expanduser().resolve(strict=True)
    if os.name != "nt":
        return f"device:{resolved.stat().st_dev}"
    import ctypes

    kernel32 = ctypes.windll.kernel32
    mount = ctypes.create_unicode_buffer(32768)
    if not kernel32.GetVolumePathNameW(str(resolved), mount, len(mount)):
        raise BackupConformanceError("backup_volume_identity_ambiguous")
    name = ctypes.create_unicode_buffer(32768)
    if not kernel32.GetVolumeNameForVolumeMountPointW(mount.value, name, len(name)):
        raise BackupConformanceError("backup_volume_identity_ambiguous")
    return f"volume_guid:{name.value.casefold()}"


def preflight_backup_root(
    source_root: Path | str,
    backup_root: Path | str,
    *,
    volume_identity: Callable[[Path], str] = canonical_volume_identity,
) -> tuple[str, str]:
    """Prove the configured target is a writable independent local publication volume."""

    try:
        source = Path(source_root).expanduser().resolve(strict=True)
        target = Path(backup_root).expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise BackupConformanceError("backup_root_missing") from error
    source_volume = volume_identity(source)
    target_volume = volume_identity(target)
    if not source_volume or not target_volume or source_volume == target_volume:
        raise BackupConformanceError("backup_root_not_second_volume")
    probe = target / f".memrelay-backup-probe-{uuid.uuid4().hex}"
    moved = target / f"{probe.name}.published"
    try:
        _write_bytes(probe, b"memrelay-backup-atomicity-probe")
        os.replace(probe, moved)
        if moved.read_bytes() != b"memrelay-backup-atomicity-probe":
            raise BackupConformanceError("backup_atomic_rename_unverified")
        _fsync_directory(target)
    except BackupConformanceError:
        raise
    except OSError as error:
        raise BackupConformanceError("backup_root_capability_incomplete") from error
    finally:
        for path in (probe, moved):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    return source_volume, target_volume


class TerminalEvidenceBackup:
    """Publishes exactly one verified generation for an immutable terminal inventory."""

    def __init__(
        self,
        *,
        artifacts_root: Path | str,
        ledger: LedgerPort,
        ledger_path: Path | str,
        backup_root: Path | str,
        volume_identity: Callable[[Path], str] = canonical_volume_identity,
        tool_version: str = "memrelay-eval/0.1.0",
    ) -> None:
        self.artifacts_root = Path(artifacts_root).expanduser().resolve(strict=True)
        self.ledger = ledger
        self.ledger_path = Path(ledger_path).expanduser().resolve(strict=True)
        self.backup_root = Path(backup_root).expanduser().resolve(strict=True)
        self.volume_identity = volume_identity
        self.tool_version = tool_version

    def backup_terminal_run(self, *, run_id: RunId, attempt_id: AttemptId) -> BackupReceipt:
        """Snapshot, copy, verify, atomically publish, then append a typed receipt link."""

        gate = BackupConformanceGate(self.backup_root)
        try:
            source_volume, target_volume = preflight_backup_root(
                self.artifacts_root, self.backup_root, volume_identity=self.volume_identity
            )
            lock = _BackupLock(self.backup_root / ".memrelay-backup.lock")
            with lock:
                started_at = _utc_now()
                staging = self.backup_root / ".staging" / uuid.uuid4().hex
                snapshot = staging / "ledger.sqlite"
                try:
                    staging.mkdir(parents=True)
                    self.ledger.snapshot_to(str(snapshot))
                    _verify_sqlite_snapshot(snapshot)
                    inventory = self._copy_inventory(staging)
                    inventory.insert(0, _inventory_item(snapshot, "ledger.sqlite"))
                    inventory.sort(key=lambda item: str(item["path"]))
                    inventory_sha256 = sha256(canonical_json_bytes(inventory)).hexdigest()
                    generation_id = f"sha256-{inventory_sha256}"
                    highest_terminal_position = _highest_terminal_position(snapshot)
                    receipt = BackupReceipt(
                        generation_id=generation_id,
                        inventory_sha256=inventory_sha256,
                        run_id=str(run_id),
                        attempt_id=str(attempt_id),
                        source_volume=source_volume,
                        target_volume=target_volume,
                        items=tuple(inventory),
                        started_at=started_at,
                        completed_at=_utc_now(),
                        highest_terminal_position=highest_terminal_position,
                        rpo="at_most_active_in_flight_attempt",
                        tool_version=self.tool_version,
                    )
                    _write_bytes(staging / "backup-receipt.json", receipt.bytes())
                    _fsync_tree(staging)
                    self._publish_or_verify_generation(staging, receipt)
                    self._link_receipt(receipt, run_id, attempt_id)
                    gate.record_backup(receipt)
                    return receipt
                except BackupConformanceError:
                    raise
                except (LedgerDirectWriteError, OSError) as error:
                    raise BackupConformanceError("backup_copy_hash_or_receipt_failure") from error
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
        except BackupConformanceError as error:
            try:
                gate.block(error.code)
            except OSError as gate_error:
                raise BackupConformanceError("backup_gate_persistence_failure") from gate_error
            raise

    def _copy_inventory(self, staging: Path) -> list[dict[str, object]]:
        copied: list[dict[str, object]] = []
        artifact_destination = staging / "artifacts"
        for source in _artifact_inventory_files(self.artifacts_root):
            relative = source.relative_to(self.artifacts_root)
            destination = artifact_destination / relative
            _copy_hash_verified(source, destination)
            copied.append(_inventory_item(destination, f"artifacts/{relative.as_posix()}"))
        if not copied:
            raise BackupConformanceError("backup_inventory_empty")
        return copied

    def _publish_or_verify_generation(self, staging: Path, receipt: BackupReceipt) -> None:
        generations = self.backup_root / "generations"
        generations.mkdir(exist_ok=True)
        destination = generations / receipt.generation_id
        if destination.exists():
            _verify_generation(destination, receipt)
            return
        try:
            os.replace(staging, destination)
        except OSError as error:
            if error.errno not in {EACCES, EEXIST, ENOTEMPTY} or not destination.exists():
                raise BackupConformanceError("backup_generation_publish_collision") from error
            _verify_generation(destination, receipt)
        _fsync_directory(generations)
        _verify_generation(destination, receipt)

    def _link_receipt(self, receipt: BackupReceipt, run_id: RunId, attempt_id: AttemptId) -> None:
        store = FilesystemArtifactStore(self.artifacts_root)
        receipt_ref = store.put_bytes(
            receipt.bytes(), media_type="application/json", classification="backup_receipt"
        )
        store.write_manifest(
            ArtifactManifest(
                artifact_id=receipt_ref.artifact_id,
                kind="backup_receipt",
                sha256=receipt_ref.sha256,
                size_bytes=receipt_ref.size_bytes,
                media_type="application/json",
                created_at=datetime.now(UTC),
                producer_component="memrelay_eval.evidence.backup",
                producer_version=self.tool_version,
                classification="backup_receipt",
                contains_secrets=False,
                source_artifact_ids=(),
                retention_policy_id=RetentionPolicyId.new(),
                encryption=None,
                scope=ArtifactScope.ATTEMPT,
                run_id=run_id,
                attempt_id=attempt_id,
            )
        )
        self.ledger.append_artifact_link(
            ArtifactLink(receipt_ref, "backup_receipt", run_id=run_id, attempt_id=attempt_id)
        )


def restore_drill(
    *,
    backup_root: Path | str,
    generation_id: str,
    quarantine_root: Path | str,
    policy: RestorePolicy,
) -> RestoreReport:
    """Restore a generation into a new quarantine root and prove every governed read."""

    gate = BackupConformanceGate(backup_root)
    started = time.monotonic()
    source = Path(backup_root).expanduser().resolve(strict=True) / "generations" / generation_id
    quarantine = Path(quarantine_root).expanduser()
    if quarantine.exists() or source == quarantine.resolve():
        raise BackupConformanceError("restore_destination_not_empty")
    receipt = _load_receipt(source)
    if receipt.generation_id != generation_id:
        raise BackupConformanceError("restore_receipt_identity_mismatch")
    _verify_generation(source, receipt)
    staging = quarantine.with_name(f".{quarantine.name}.{uuid.uuid4().hex}.staging")
    try:
        staging.mkdir(parents=True)
        for item in receipt.items:
            relative = _inventory_relative(item)
            _copy_hash_verified(source / relative, staging / relative)
        _verify_sqlite_snapshot(staging / "ledger.sqlite")
        store = FilesystemArtifactStore(staging / "artifacts")
        manifests = store.verified_manifests()
        for manifest in manifests:
            if not policy.permits(manifest):
                raise BackupConformanceError("restore_policy_rejected_artifact")
        reachability = store.rebuild_reachability()
        if reachability.orphaned_blobs:
            raise BackupConformanceError("restore_orphaned_artifact")
        linked = _verify_restored_ledger_links(staging / "ledger.sqlite", store)
        elapsed = int(time.monotonic() - started)
        if elapsed > _RTO_SECONDS:
            raise BackupConformanceError("restore_rto_exceeded")
        report = RestoreReport(
            generation_id=receipt.generation_id,
            inventory_sha256=receipt.inventory_sha256,
            restore_root=str(quarantine),
            elapsed_seconds=elapsed,
            verified_item_count=len(receipt.items),
            verified_ledger_link_count=linked,
            policy_version=policy.version,
            completed_at=_utc_now(),
        )
        _write_bytes(staging / "restore-report.json", canonical_json_bytes(report.to_document()))
        _fsync_tree(staging)
        os.replace(staging, quarantine)
        _fsync_directory(quarantine.parent)
        gate.record_restore(report)
        return report
    except BackupConformanceError as error:
        try:
            gate.block(error.code)
        except OSError as gate_error:
            raise BackupConformanceError("backup_gate_persistence_failure") from gate_error
        raise
    except (LedgerDirectWriteError, OSError, ValueError) as error:
        raise BackupConformanceError("restore_copy_hash_or_index_failure") from error
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _artifact_inventory_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file():
            continue
        if path.is_symlink() or any(
            part in _IGNORED_ARTIFACT_DIRECTORIES for part in relative.parts
        ):
            if path.is_symlink():
                raise BackupConformanceError("backup_source_symlink_ambiguous")
            continue
        if any(part.startswith(".") and part.endswith(".staging") for part in relative.parts):
            raise BackupConformanceError("backup_source_partial_staging_present")
        yield path


def _copy_hash_verified(source: Path, destination: Path) -> None:
    before = source.stat()
    if not source.is_file() or source.is_symlink():
        raise BackupConformanceError("backup_source_not_regular_file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256()
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise BackupConformanceError("backup_source_changed_during_copy")
    copied = _hash_file(destination)
    if copied != digest.hexdigest() or destination.stat().st_size != before.st_size:
        raise BackupConformanceError("backup_copy_hash_mismatch")


def _inventory_item(path: Path, relative_path: str) -> dict[str, object]:
    return {
        "path": relative_path,
        "sha256": _hash_file(path),
        "size_bytes": path.stat().st_size,
    }


def verify_backup_generation(*, backup_root: Path | str, generation_id: str) -> BackupReceipt:
    """Verify an immutable backup generation without restoring or changing it."""
    source = Path(backup_root).expanduser().resolve(strict=True) / "generations" / generation_id
    receipt = _load_receipt(source)
    if receipt.generation_id != generation_id:
        raise BackupConformanceError("restore_receipt_identity_mismatch")
    _verify_generation(source, receipt)
    return receipt


def _verify_generation(generation: Path, receipt: BackupReceipt) -> None:
    expected = receipt.bytes()
    receipt_path = generation / "backup-receipt.json"
    if not receipt_path.is_file() or receipt_path.read_bytes() != expected:
        raise BackupConformanceError("backup_stale_or_tampered_receipt")
    actual_inventory: list[dict[str, object]] = []
    for item in receipt.items:
        relative = _inventory_relative(item)
        path = generation / relative
        if not path.is_file() or path.is_symlink():
            raise BackupConformanceError("backup_generation_incomplete")
        actual_inventory.append(_inventory_item(path, relative.as_posix()))
    actual_inventory.sort(key=lambda item: str(item["path"]))
    if canonical_json_bytes(actual_inventory) != canonical_json_bytes(list(receipt.items)):
        raise BackupConformanceError("backup_generation_hash_mismatch")
    if sha256(canonical_json_bytes(actual_inventory)).hexdigest() != receipt.inventory_sha256:
        raise BackupConformanceError("backup_inventory_hash_mismatch")


def _load_receipt(generation: Path) -> BackupReceipt:
    try:
        raw = (generation / "backup-receipt.json").read_bytes()
        document = json.loads(raw)
        if (
            canonical_json_bytes(document) != raw
            or document["schema_version"] != _RECEIPT_SCHEMA_VERSION
        ):
            raise ValueError
        return BackupReceipt(
            generation_id=document["generation_id"],
            inventory_sha256=document["inventory_sha256"],
            run_id=document["run_id"],
            attempt_id=document["attempt_id"],
            source_volume=document["source_volume"],
            target_volume=document["target_volume"],
            items=tuple(document["items"]),
            started_at=document["started_at"],
            completed_at=document["completed_at"],
            highest_terminal_position=document["highest_terminal_position"],
            rpo=document["rpo"],
            tool_version=document["tool_version"],
            schema_version=document["schema_version"],
        )
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BackupConformanceError("backup_receipt_malformed") from error


def _inventory_relative(item: dict[str, object]) -> Path:
    value = item.get("path")
    if not isinstance(value, str):
        raise BackupConformanceError("backup_receipt_inventory_malformed")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise BackupConformanceError("backup_receipt_path_escape")
    return relative


def _verify_sqlite_snapshot(path: Path) -> None:
    try:
        verify_snapshot_file(path)
    except LedgerDirectWriteError as error:
        raise BackupConformanceError(str(error)) from error


def _highest_terminal_position(snapshot: Path) -> int:
    try:
        return verify_snapshot_file(snapshot)
    except LedgerDirectWriteError as error:
        raise BackupConformanceError(str(error)) from error


def _verify_restored_ledger_links(snapshot: Path, store: FilesystemArtifactStore) -> int:
    def verify(reference: ArtifactRef) -> None:
        store.open_verified(reference)
        store.read_manifest(reference)

    try:
        return verify_snapshot_artifact_links(snapshot, verify)
    except LedgerDirectWriteError as error:
        raise BackupConformanceError(str(error)) from error


class _BackupLock:
    """Kernel-backed generation lease; a stale marker can never retain ownership."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> _BackupLock:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a+b")
            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write(b"\0")
                self._handle.flush()
            self._handle.seek(0)
        except OSError as error:
            if hasattr(self, "_handle") and not self._handle.closed:
                self._handle.close()
            raise BackupConformanceError("backup_lock_acquisition_failed") from error
        try:
            if os.name == "nt":
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if not self._handle.closed:
                self._handle.close()
            code = (
                "backup_concurrent_writer_detected"
                if error.errno in {EACCES, EAGAIN}
                else "backup_lock_acquisition_failed"
            )
            raise BackupConformanceError(code) from error
        return self

    def __exit__(self, *_: object) -> None:
        try:
            self._handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_tree(root: Path) -> None:
    for directory, _, _ in os.walk(root):
        _fsync_directory(Path(directory))


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise
    finally:
        os.close(descriptor)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
