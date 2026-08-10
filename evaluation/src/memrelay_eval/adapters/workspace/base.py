"""Provider-neutral isolated workspace contract and compensating cleanup."""

from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from ...canonical import canonical_bytes
from ...domain.entities import ArtifactLink, ArtifactRef, TelemetryObservation
from ...domain.ids import AttemptId, RunId
from ..fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry


class WorkspaceProviderError(RuntimeError):
    """A provider cannot complete its isolated-workspace contract."""


class WorkspaceCollisionError(WorkspaceProviderError):
    """An allocation is unsafe because it overlaps or reuses a root."""


class WorkspacePathSafetyError(WorkspaceCollisionError):
    """An authority path contains a symlink, junction, or other reparse point."""


@dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    """Frozen, treatment-neutral inputs used to allocate one attempt workspace."""

    attempt_id: AttemptId
    run_id: RunId
    source_root: Path
    frozen_revision: str
    source_content_sha256: str
    allocation_root: Path
    require_clean_source: bool = True

    def __post_init__(self) -> None:
        if not self.frozen_revision:
            raise WorkspaceCollisionError("a frozen revision is required")
        if len(self.source_content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_content_sha256
        ):
            raise WorkspaceCollisionError("source content hash must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class WorkspaceHandle:
    """All fresh host-native state allocated to one opaque attempt."""

    attempt_id: AttemptId
    run_id: RunId
    provider_name: str
    allocation_root: Path
    attempt_root: Path
    workspace_root: Path
    agent_session_root: Path
    cache_root: Path
    staging_root: Path
    telemetry_root: Path
    memrelay_home: Path
    graph_root: Path
    spool_root: Path
    socket_path: Path
    config_root: Path
    private_git_dir: Path | None
    telemetry_identity: str
    frozen_revision: str
    source_content_sha256: str
    source_root: Path

    @property
    def mutable_roots(self) -> tuple[Path, ...]:
        roots = (
            self.workspace_root,
            self.agent_session_root,
            self.cache_root,
            self.staging_root,
            self.telemetry_root,
            self.memrelay_home,
            self.graph_root,
            self.spool_root,
            self.socket_path.parent,
            self.config_root,
        )
        return roots if self.private_git_dir is None else (*roots, self.private_git_dir)

    @property
    def environment(self) -> dict[str, str]:
        return {
            "MEMRELAY_HOME": str(self.memrelay_home),
            "XDG_CACHE_HOME": str(self.cache_root),
            "XDG_CONFIG_HOME": str(self.config_root),
        }

    def parity_layout(self) -> dict[str, object]:
        """Project fixed workspace topology without persisting attempt-specific paths."""
        return {
            "provider": self.provider_name,
            "roots": [
                str(root.relative_to(self.attempt_root)).replace("\\", "/")
                for root in self.mutable_roots
            ],
            "private_git": self.private_git_dir is not None,
            "frozen_revision": self.frozen_revision,
            "source_content_sha256": self.source_content_sha256,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """Immutable detached snapshot identity; no mutable workspace path is retained."""

    revision: str
    source_content_sha256: str
    workspace_content_sha256: str
    baseline_revision: str | None = None
    baseline_files_artifact: ArtifactRef | None = None
    terminal_files_artifact: ArtifactRef | None = None
    patch_artifact: ArtifactRef | None = None
    canonical_artifact: ArtifactRef | None = None
    canonical_sha256: str | None = None
    attempt_id: AttemptId | None = None
    run_id: RunId | None = None


@dataclass(frozen=True, slots=True)
class CleanupRecord:
    """Append-only evidence for a single compensating cleanup attempt."""

    attempt_id: AttemptId
    provider_name: str
    occurred_at: datetime
    succeeded: bool
    quarantined: bool
    already_clean: bool
    steps: tuple[str, ...]
    error: str | None = None


class _Materializer(Protocol):
    def _materialize(self, handle: WorkspaceHandle, spec: WorkspaceSpec) -> None: ...

    def _remove_workspace(self, handle: WorkspaceHandle) -> None: ...


class BaseWorkspaceProvider:
    """Common contract shared by every host-native workspace topology."""

    provider_name = "base"

    def __init__(
        self,
        *,
        artifact_store: InMemoryArtifactStore | None = None,
        ledger: InMemoryLedger | None = None,
        telemetry: InMemoryTelemetry | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.artifact_store = artifact_store or InMemoryArtifactStore()
        self.ledger = ledger or InMemoryLedger()
        self.telemetry = telemetry or InMemoryTelemetry()
        self._fault_injector = fault_injector
        self._cleanup_records: list[CleanupRecord] = []
        self._handles: dict[AttemptId, WorkspaceHandle] = {}
        self._lock = asyncio.Lock()

    @property
    def cleanup_records(self) -> tuple[CleanupRecord, ...]:
        return tuple(self._cleanup_records)

    async def create(self, spec: WorkspaceSpec) -> WorkspaceHandle:
        async with self._lock:
            _source_root, allocation_root = self._validate_spec(spec)
            self._prepare_allocation_root(allocation_root)
            self._assert_registry_paths_safe(allocation_root)
            attempt_root = self._create_private_attempt_root(spec)
            handle = self._build_handle(spec, attempt_root)
            ownership = self._ownership_path(allocation_root, spec.attempt_id)
            try:
                self._claim_ownership(ownership, allocation_root, spec.attempt_id, attempt_root)
            except WorkspaceProviderError:
                shutil.rmtree(attempt_root, onerror=self._clear_readonly_and_retry)
                raise
            try:
                self._make_private_directories(handle)
                self._inject("create_before_materialize")
                self._materialize(handle, spec)
                self._verify_materialization(handle, spec)
                self._inject("create_after_materialize")
                self._record_evidence(handle, "workspace_ownership")
                self._handles[handle.attempt_id] = handle
                return handle
            except Exception as error:
                cleanup = self._cleanup(handle, already_clean=False)
                self._cleanup_records.append(cleanup)
                self._record_cleanup_evidence(cleanup, handle)
                raise WorkspaceProviderError(
                    f"{self.provider_name} provisioning failed before exposure"
                ) from error

    async def freeze(self, handle: WorkspaceHandle) -> WorkspaceSnapshot:
        self._assert_managed(handle)
        if not handle.workspace_root.exists():
            raise WorkspaceProviderError("cannot freeze a cleaned workspace")
        revision = self._git(handle.workspace_root, "rev-parse", "HEAD")
        content_hash = self._archive_hash(handle.workspace_root, revision)
        baseline_files = self._snapshot_file_bytes(handle.source_root)
        terminal_files = self._snapshot_file_bytes(handle.workspace_root)
        patch_bytes = self._git_bytes(
            handle.workspace_root, "diff", "--binary", handle.frozen_revision
        )
        baseline_files_artifact = self.artifact_store.put_bytes(
            baseline_files, media_type="application/json", classification="workspace_snapshot"
        )
        terminal_files_artifact = self.artifact_store.put_bytes(
            terminal_files, media_type="application/json", classification="workspace_snapshot"
        )
        patch_artifact = self.artifact_store.put_bytes(
            patch_bytes, media_type="text/x-diff", classification="workspace_patch"
        )
        snapshot_document = canonical_bytes(
            {
                "schema_version": "1.0.0",
                "normalization": {
                    "paths": "relative_posix",
                    "timestamps": "omitted",
                    "file_order": "codepoint",
                    "reparse_points": "rejected",
                },
                "baseline_revision": handle.frozen_revision,
                "terminal_revision": revision,
                "baseline_files_sha256": baseline_files_artifact.sha256,
                "terminal_files_sha256": terminal_files_artifact.sha256,
                "patch_sha256": patch_artifact.sha256,
            }
        )
        canonical_artifact = self.artifact_store.put_bytes(
            snapshot_document, media_type="application/json", classification="workspace_snapshot"
        )
        snapshot = WorkspaceSnapshot(
            revision=revision,
            source_content_sha256=handle.source_content_sha256,
            workspace_content_sha256=content_hash,
            baseline_revision=handle.frozen_revision,
            baseline_files_artifact=baseline_files_artifact,
            terminal_files_artifact=terminal_files_artifact,
            patch_artifact=patch_artifact,
            canonical_artifact=canonical_artifact,
            canonical_sha256=canonical_artifact.sha256,
            attempt_id=handle.attempt_id,
            run_id=handle.run_id,
        )
        self._record_evidence(handle, "workspace_snapshot")
        return snapshot

    async def destroy(self, handle: WorkspaceHandle) -> CleanupRecord:
        async with self._lock:
            self._assert_managed(handle)
            record = self._cleanup(
                handle, already_clean=not self._path_exists_or_reparse_point(handle.attempt_root)
            )
            self._cleanup_records.append(record)
            self._record_cleanup_evidence(record, handle)
            return record

    def _validate_spec(self, spec: WorkspaceSpec) -> tuple[Path, Path]:
        source_root = self._absolute_path(spec.source_root)
        allocation_root = self._absolute_path(spec.allocation_root)
        self._assert_safe_authority_path(source_root, "source root")
        self._assert_safe_authority_path(allocation_root, "allocation root")
        if not source_root.is_dir() or not (source_root / ".git").exists():
            raise WorkspaceCollisionError("source root must be a local Git checkout")
        if allocation_root == source_root or allocation_root.is_relative_to(source_root):
            raise WorkspaceCollisionError("allocation root must not be inside the source checkout")
        if source_root.is_relative_to(allocation_root):
            raise WorkspaceCollisionError("allocation root must not contain the source checkout")
        if spec.require_clean_source and self._git(source_root, "status", "--porcelain"):
            raise WorkspaceCollisionError("frozen source checkout must be clean")
        resolved_revision = self._git(
            source_root, "rev-parse", f"{spec.frozen_revision}^{{commit}}"
        )
        if resolved_revision != spec.frozen_revision:
            raise WorkspaceCollisionError("frozen revision must resolve to its immutable commit")
        if self._archive_hash(source_root, resolved_revision) != spec.source_content_sha256:
            raise WorkspaceCollisionError("source content does not match the frozen source hash")
        return source_root, allocation_root

    def _build_handle(self, spec: WorkspaceSpec, attempt_root: Path) -> WorkspaceHandle:
        digest = sha256(str(spec.attempt_id).encode("ascii")).hexdigest()
        return WorkspaceHandle(
            attempt_id=spec.attempt_id,
            run_id=spec.run_id,
            provider_name=self.provider_name,
            allocation_root=spec.allocation_root.absolute(),
            attempt_root=attempt_root,
            workspace_root=attempt_root / "workspace",
            agent_session_root=attempt_root / "agent-session",
            cache_root=attempt_root / "cache",
            staging_root=attempt_root / "staging",
            telemetry_root=attempt_root / "telemetry",
            memrelay_home=attempt_root / "memrelay-home",
            graph_root=attempt_root / "graph",
            spool_root=attempt_root / "spool",
            socket_path=attempt_root / "socket" / "attempt.sock",
            config_root=attempt_root / "config",
            private_git_dir=attempt_root / "private-git",
            telemetry_identity=f"telemetry_{digest[:32]}",
            frozen_revision=spec.frozen_revision,
            source_content_sha256=spec.source_content_sha256,
            source_root=spec.source_root.resolve(),
        )

    def _create_private_attempt_root(self, spec: WorkspaceSpec) -> Path:
        digest = sha256(str(spec.attempt_id).encode("ascii")).hexdigest()
        temporary_root = Path(tempfile.mkdtemp(prefix=f"memrelay-{digest[:16]}-"))
        try:
            self._assert_safe_authority_path(temporary_root, "private attempt root")
        except WorkspacePathSafetyError:
            shutil.rmtree(temporary_root, onerror=self._clear_readonly_and_retry)
            raise
        return temporary_root

    def _ownership_path(self, allocation_root: Path, attempt_id: AttemptId) -> Path:
        return allocation_root / f".workspace-ownership-{attempt_id}.json"

    def _claim_ownership(
        self, ownership: Path, allocation_root: Path, attempt_id: AttemptId, attempt_root: Path
    ) -> None:
        self._assert_safe_authority_path(ownership, "workspace ownership record")
        # A local, exclusive sidecar used only to block unsafe workspace reuse.
        payload = json.dumps(
            {"attempt_id": str(attempt_id), "attempt_root": str(attempt_root)},
            sort_keys=True,
            separators=(",", ":"),
        )
        self._write_private_json_exclusive(
            ownership, allocation_root, "workspace ownership record", payload
        )

    def _make_private_directories(self, handle: WorkspaceHandle) -> None:
        for root in handle.mutable_roots[1:]:
            self._create_attempt_root(root, handle.attempt_root, "attempt-local root")

    def _verify_materialization(self, handle: WorkspaceHandle, spec: WorkspaceSpec) -> None:
        self._assert_safe_authority_path(handle.workspace_root, "workspace root")
        if self._git(handle.workspace_root, "rev-parse", "HEAD") != spec.frozen_revision:
            raise WorkspaceProviderError("provider did not materialize the frozen revision")
        if (
            self._archive_hash(handle.workspace_root, spec.frozen_revision)
            != spec.source_content_sha256
        ):
            raise WorkspaceProviderError("provider did not materialize the frozen source content")

    def _cleanup(self, handle: WorkspaceHandle, *, already_clean: bool) -> CleanupRecord:
        steps: list[str] = []
        error: str | None = None
        try:
            if not already_clean:
                self._assert_safe_authority_path(handle.attempt_root, "attempt cleanup root")
                self._inject("cleanup_before_remove")
                self._remove_workspace(handle)
                if handle.attempt_root.exists():
                    self._remove_attempt_tree(handle.attempt_root)
                steps.extend(("provider_workspace_removed", "attempt_roots_removed"))
            else:
                steps.append("attempt_roots_already_clean")
            return CleanupRecord(
                handle.attempt_id,
                self.provider_name,
                datetime.now(UTC),
                True,
                False,
                already_clean,
                tuple(steps),
            )
        except Exception as caught:
            error = f"{type(caught).__name__}: {caught}"
            quarantined = False
            try:
                self._mark_quarantine(handle, error)
                quarantined = True
            except (WorkspaceProviderError, OSError) as quarantine_error:
                error = f"{error}; quarantine refused: {quarantine_error}"
            return CleanupRecord(
                handle.attempt_id,
                self.provider_name,
                datetime.now(UTC),
                False,
                quarantined,
                already_clean,
                tuple(steps),
                error,
            )

    def _mark_quarantine(self, handle: WorkspaceHandle, error: str) -> None:
        allocation_root = handle.allocation_root
        self._assert_registry_paths_safe(allocation_root)
        marker = allocation_root / f".workspace-quarantine-{handle.attempt_id}.json"
        self._assert_safe_authority_path(marker, "workspace quarantine record")
        if self._path_exists_or_reparse_point(marker):
            return
        # A local quarantine sidecar can contain arbitrary OS error text and has no identity role.
        payload = json.dumps(
            {"attempt_id": str(handle.attempt_id), "error": error},
            sort_keys=True,
            separators=(",", ":"),
        )
        self._write_private_json_exclusive(
            marker, allocation_root, "workspace quarantine record", payload
        )

    def _record_evidence(self, handle: WorkspaceHandle, purpose: str) -> ArtifactRef:
        payload = canonical_bytes(
            {
                "attempt_id": str(handle.attempt_id),
                "provider": handle.provider_name,
                "purpose": purpose,
                "revision": handle.frozen_revision,
                "source_content_sha256": handle.source_content_sha256,
            },
        )
        artifact = self.artifact_store.put_bytes(
            payload, media_type="application/json", classification="unpaid_conformance"
        )
        self.ledger.append_artifact_link(
            ArtifactLink(artifact, purpose, run_id=handle.run_id, attempt_id=handle.attempt_id)
        )
        self.telemetry.emit(
            TelemetryObservation(
                "workspace_evidence_recorded", datetime.now(UTC), {"bytes": len(payload)}
            )
        )
        return artifact

    def _record_cleanup_evidence(
        self, record: CleanupRecord, handle: WorkspaceHandle
    ) -> ArtifactRef:
        payload = canonical_bytes(
            {
                "attempt_id": str(record.attempt_id),
                "provider": record.provider_name,
                "succeeded": record.succeeded,
                "quarantined": record.quarantined,
                "already_clean": record.already_clean,
                "steps": record.steps,
            },
        )
        artifact = self.artifact_store.put_bytes(
            payload, media_type="application/json", classification="unpaid_conformance"
        )
        self.ledger.append_artifact_link(
            ArtifactLink(
                artifact,
                "workspace_cleanup",
                run_id=handle.run_id,
                attempt_id=handle.attempt_id,
            )
        )
        self.telemetry.emit(
            TelemetryObservation(
                "workspace_cleanup_recorded", record.occurred_at, {"succeeded": record.succeeded}
            )
        )
        return artifact

    def _assert_managed(self, handle: WorkspaceHandle) -> None:
        known = self._handles.get(handle.attempt_id)
        if known != handle:
            raise WorkspaceProviderError("workspace handle is not owned by this provider")

    def _inject(self, step: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(step)

    @staticmethod
    def _git(path: Path, *arguments: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(path), *arguments],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            raise WorkspaceProviderError(error.stderr.strip() or "Git command failed") from error
        return completed.stdout.strip()

    @staticmethod
    def _archive_hash(path: Path, revision: str) -> str:
        try:
            archive = subprocess.run(
                ["git", "-C", str(path), "archive", "--format=tar", revision],
                check=True,
                capture_output=True,
            ).stdout
        except subprocess.CalledProcessError as error:
            raise WorkspaceProviderError(error.stderr.decode(errors="replace").strip()) from error
        return sha256(archive).hexdigest()

    @staticmethod
    def _git_bytes(path: Path, *arguments: str) -> bytes:
        try:
            return subprocess.run(
                ["git", "-C", str(path), *arguments],
                check=True,
                capture_output=True,
            ).stdout
        except subprocess.CalledProcessError as error:
            raise WorkspaceProviderError(error.stderr.decode(errors="replace").strip()) from error

    def _snapshot_file_bytes(self, root: Path) -> bytes:
        """Capture regular workspace files with no clock or host-path authority."""
        self._assert_safe_authority_path(root, "workspace snapshot root")
        files: list[dict[str, object]] = []
        for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            retained_directories: list[str] = []
            for directory in sorted(directories):
                if directory == ".git":
                    continue
                if self._is_reparse_point(current_path / directory):
                    raise WorkspacePathSafetyError(
                        "workspace snapshot contains a symlink or reparse point: "
                        f"{current_path / directory}"
                    )
                retained_directories.append(directory)
            directories[:] = retained_directories
            for filename in sorted(filenames):
                candidate = current_path / filename
                relative = candidate.relative_to(root)
                if relative.parts and relative.parts[0] == ".git":
                    continue
                if self._is_reparse_point(candidate):
                    raise WorkspacePathSafetyError(
                        f"workspace snapshot contains a symlink or reparse point: {candidate}"
                    )
                status = candidate.stat()
                if not stat.S_ISREG(status.st_mode):
                    raise WorkspacePathSafetyError(
                        f"workspace snapshot contains non-regular file: {candidate}"
                    )
                data = candidate.read_bytes()
                files.append(
                    {
                        "path": relative.as_posix(),
                        "mode": stat.S_IMODE(status.st_mode),
                        "sha256": sha256(data).hexdigest(),
                        "content_base64": base64.b64encode(data).decode("ascii"),
                    }
                )
        return canonical_bytes({"schema_version": "1.0.0", "files": files})

    def _clear_readonly_and_retry(
        self,
        operation: Callable[[str], None],
        path: str,
        exception_info: tuple[object, object, object],
    ) -> None:
        del exception_info
        self._assert_safe_authority_path(Path(path), "cleanup path")
        os.chmod(path, stat.S_IWRITE)
        operation(path)

    def _remove_attempt_tree(self, root: Path) -> None:
        self._assert_safe_authority_path(root, "attempt cleanup root")
        for entry in os.scandir(root):
            child = Path(entry.path)
            if self._is_reparse_point(child):
                raise WorkspacePathSafetyError(
                    f"attempt cleanup root contains a symlink or reparse point: {child}"
                )
            if entry.is_dir(follow_symlinks=False):
                self._remove_attempt_tree(child)
            else:
                try:
                    os.unlink(child)
                except PermissionError:
                    os.chmod(child, stat.S_IWRITE)
                    os.unlink(child)
        os.rmdir(root)

    @staticmethod
    def _absolute_path(path: Path) -> Path:
        return Path(os.path.abspath(path))

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        try:
            path_status = path.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise WorkspacePathSafetyError(f"cannot inspect path safety for {path}") from error
        junction = getattr(path, "is_junction", None)
        is_junction = callable(junction) and junction()
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        has_reparse_attribute = bool(
            getattr(path_status, "st_file_attributes", 0) & reparse_attribute
        )
        return path.is_symlink() or is_junction or has_reparse_attribute

    @staticmethod
    def _path_exists_or_reparse_point(path: Path) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise WorkspacePathSafetyError(f"cannot inspect path safety for {path}") from error
        return True

    def _assert_safe_authority_path(self, path: Path, label: str) -> Path:
        absolute_path = self._absolute_path(path)
        current = absolute_path
        while True:
            if self._is_reparse_point(current):
                raise WorkspacePathSafetyError(
                    f"{label} contains a symlink or reparse point: {current}"
                )
            if current.parent == current:
                return absolute_path
            current = current.parent

    def _assert_registry_paths_safe(self, allocation_root: Path) -> None:
        self._assert_safe_authority_path(
            allocation_root / ".workspace-ownership", "workspace ownership registry"
        )
        self._assert_safe_authority_path(
            allocation_root / ".workspace-quarantine", "workspace quarantine registry"
        )

    def _prepare_allocation_root(self, allocation_root: Path) -> None:
        self._assert_safe_authority_path(allocation_root, "allocation root")
        allocation_root.mkdir(parents=True, exist_ok=True)
        self._assert_safe_authority_path(allocation_root, "allocation root")

    def _create_attempt_root(self, root: Path, attempt_root: Path, label: str) -> None:
        self._assert_attempt_child(root, attempt_root, label)
        root.mkdir(parents=True, exist_ok=False)
        self._assert_safe_authority_path(root, label)

    def _assert_attempt_child(self, path: Path, attempt_root: Path, label: str) -> Path:
        authority = self._assert_safe_authority_path(attempt_root, "attempt authority")
        candidate = self._assert_safe_authority_path(path, label)
        if not candidate.is_relative_to(authority):
            raise WorkspacePathSafetyError(f"{label} escapes attempt authority")
        return candidate

    def _write_private_json_exclusive(
        self, path: Path, authority_root: Path, label: str, payload: str
    ) -> None:
        authority = self._assert_safe_authority_path(authority_root, "allocation authority")
        candidate = self._assert_safe_authority_path(path, label)
        if not candidate.is_relative_to(authority) or candidate.parent != authority:
            raise WorkspacePathSafetyError(f"{label} escapes allocation authority")
        if os.name == "nt":
            self._write_windows_relative_json(authority, candidate.name, label, payload)
            return
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(candidate, flags)
        except FileExistsError as error:
            raise WorkspaceCollisionError(f"{label} already exists") from error
        try:
            self._assert_safe_authority_path(authority_root, "allocation authority")
            self._assert_safe_authority_path(path.parent, label)
            os.write(descriptor, payload.encode("utf-8"))
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_windows_relative_json(
        authority_root: Path, filename: str, label: str, payload: str
    ) -> None:
        from ctypes import wintypes

        file_attribute_reparse_point = 0x400
        file_attribute_normal = 0x80
        file_attribute_tag_info = 9
        file_create = 2
        file_non_directory_file = 0x40
        file_open_reparse_point = 0x00200000
        file_synchronous_io_nonalert = 0x20
        generic_write = 0x40000000
        synchronize = 0x00100000
        invalid_handle_value = wintypes.HANDLE(-1).value
        obj_case_insensitive = 0x40
        share_read_write_delete = 0x00000007

        class FileAttributeTagInfo(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD)]

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.USHORT),
                ("maximum_length", wintypes.USHORT),
                ("buffer", wintypes.LPWSTR),
            ]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.ULONG),
                ("root_directory", wintypes.HANDLE),
                ("object_name", ctypes.POINTER(UnicodeString)),
                ("attributes", wintypes.ULONG),
                ("security_descriptor", wintypes.LPVOID),
                ("security_quality_of_service", wintypes.LPVOID),
            ]

        class IoStatusBlock(ctypes.Structure):
            _fields_ = [("status", wintypes.LONG), ("information", ctypes.c_size_t)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        directory_handle = create_file(
            str(authority_root),
            generic_write,
            share_read_write_delete,
            None,
            3,
            0x02200000,
            None,
        )
        if directory_handle == invalid_handle_value:
            raise WorkspacePathSafetyError(f"cannot open allocation authority: {authority_root}")
        try:
            attribute_info = FileAttributeTagInfo()
            if not kernel32.GetFileInformationByHandleEx(
                directory_handle,
                file_attribute_tag_info,
                ctypes.byref(attribute_info),
                ctypes.sizeof(attribute_info),
            ):
                raise WorkspacePathSafetyError(
                    f"cannot inspect allocation authority: {authority_root}"
                )
            if attribute_info.attributes & file_attribute_reparse_point:
                raise WorkspacePathSafetyError(
                    f"allocation authority contains a symlink or reparse point: {authority_root}"
                )
            filename_buffer = ctypes.create_unicode_buffer(filename)
            unicode_name = UnicodeString(
                len(filename) * ctypes.sizeof(ctypes.c_wchar),
                (len(filename) + 1) * ctypes.sizeof(ctypes.c_wchar),
                ctypes.cast(filename_buffer, wintypes.LPWSTR),
            )
            object_attributes = ObjectAttributes(
                ctypes.sizeof(ObjectAttributes),
                directory_handle,
                ctypes.pointer(unicode_name),
                obj_case_insensitive,
                None,
                None,
            )
            status_block = IoStatusBlock()
            file_handle = wintypes.HANDLE()
            nt_create_file = ctypes.WinDLL("ntdll").NtCreateFile
            nt_create_file.argtypes = [
                ctypes.POINTER(wintypes.HANDLE),
                wintypes.ULONG,
                ctypes.POINTER(ObjectAttributes),
                ctypes.POINTER(IoStatusBlock),
                wintypes.LPVOID,
                wintypes.ULONG,
                wintypes.ULONG,
                wintypes.ULONG,
                wintypes.ULONG,
                wintypes.LPVOID,
                wintypes.ULONG,
            ]
            nt_create_file.restype = wintypes.LONG
            status = nt_create_file(
                ctypes.byref(file_handle),
                generic_write | synchronize,
                ctypes.byref(object_attributes),
                ctypes.byref(status_block),
                None,
                file_attribute_normal,
                share_read_write_delete,
                file_create,
                file_non_directory_file | file_open_reparse_point | file_synchronous_io_nonalert,
                None,
                0,
            )
            if status != 0:
                if status & 0xFFFFFFFF == 0xC0000035:
                    raise WorkspaceCollisionError(f"{label} already exists")
                status_hex = f"0x{status & 0xFFFFFFFF:08X}"
                raise WorkspaceProviderError(
                    f"cannot create {label} in allocation authority (NTSTATUS {status_hex})"
                )
            try:
                encoded_payload = payload.encode("utf-8")
                written = wintypes.DWORD()
                if not kernel32.WriteFile(
                    file_handle,
                    encoded_payload,
                    len(encoded_payload),
                    ctypes.byref(written),
                    None,
                ) or written.value != len(encoded_payload):
                    raise WorkspaceProviderError(f"cannot write {label} in allocation authority")
            finally:
                kernel32.CloseHandle(file_handle)
        finally:
            kernel32.CloseHandle(directory_handle)

    def _materialize(self, handle: WorkspaceHandle, spec: WorkspaceSpec) -> None:
        raise NotImplementedError

    def _remove_workspace(self, handle: WorkspaceHandle) -> None:
        raise NotImplementedError
