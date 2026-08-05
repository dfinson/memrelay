"""Provider-neutral isolated workspace contract and compensating cleanup."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from ...domain.entities import ArtifactLink, ArtifactRef, TelemetryObservation
from ...domain.ids import AttemptId, RunId
from ..fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry


class WorkspaceProviderError(RuntimeError):
    """A provider cannot complete its isolated-workspace contract."""


class WorkspaceCollisionError(WorkspaceProviderError):
    """An allocation is unsafe because it overlaps or reuses a root."""


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


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """Immutable source/workspace identity taken before any execution."""

    revision: str
    source_content_sha256: str
    workspace_content_sha256: str


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
            attempt_root = allocation_root / str(spec.attempt_id)
            ownership = self._ownership_path(allocation_root, spec.attempt_id)
            if attempt_root.exists() or attempt_root.is_symlink():
                raise WorkspaceCollisionError(
                    "attempt root already exists and cannot be safely claimed"
                )
            self._claim_ownership(ownership, attempt_root)
            handle = self._build_handle(spec, attempt_root)
            try:
                attempt_root.mkdir(parents=True, exist_ok=False)
            except FileExistsError as error:
                raise WorkspaceCollisionError(
                    "attempt root was concurrently claimed by another allocator"
                ) from error
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
        snapshot = WorkspaceSnapshot(
            revision=revision,
            source_content_sha256=handle.source_content_sha256,
            workspace_content_sha256=content_hash,
        )
        self._record_evidence(handle, "workspace_snapshot")
        return snapshot

    async def destroy(self, handle: WorkspaceHandle) -> CleanupRecord:
        async with self._lock:
            self._assert_managed(handle)
            record = self._cleanup(handle, already_clean=not handle.attempt_root.exists())
            self._cleanup_records.append(record)
            self._record_cleanup_evidence(record, handle)
            return record

    def _validate_spec(self, spec: WorkspaceSpec) -> tuple[Path, Path]:
        source_root = spec.source_root.resolve(strict=True)
        allocation_root = spec.allocation_root.resolve(strict=False)
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

    def _ownership_path(self, allocation_root: Path, attempt_id: AttemptId) -> Path:
        return allocation_root / ".workspace-ownership" / f"{attempt_id}.json"

    def _claim_ownership(self, ownership: Path, attempt_root: Path) -> None:
        ownership.parent.mkdir(parents=True, exist_ok=True)
        if ownership.parent.is_symlink() or ownership.is_symlink():
            raise WorkspaceCollisionError("ownership registry may not be a symlink or junction")
        payload = json.dumps(
            {"attempt_id": attempt_root.name, "attempt_root": str(attempt_root)},
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            descriptor = os.open(ownership, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise WorkspaceCollisionError("attempt identity was already allocated") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(payload)

    def _make_private_directories(self, handle: WorkspaceHandle) -> None:
        for root in handle.mutable_roots[1:]:
            root.mkdir(parents=True, exist_ok=False)
            if root.is_symlink():
                raise WorkspaceCollisionError("attempt root may not be a symlink or junction")

    def _verify_materialization(self, handle: WorkspaceHandle, spec: WorkspaceSpec) -> None:
        if handle.workspace_root.is_symlink():
            raise WorkspaceCollisionError("workspace root may not be a symlink or junction")
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
                self._inject("cleanup_before_remove")
                self._remove_workspace(handle)
                if handle.attempt_root.exists():
                    shutil.rmtree(handle.attempt_root, onerror=self._clear_readonly_and_retry)
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
            self._mark_quarantine(handle, error)
            return CleanupRecord(
                handle.attempt_id,
                self.provider_name,
                datetime.now(UTC),
                False,
                True,
                already_clean,
                tuple(steps),
                error,
            )

    def _mark_quarantine(self, handle: WorkspaceHandle, error: str) -> None:
        marker = handle.attempt_root.parent / ".workspace-quarantine" / f"{handle.attempt_id}.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {"attempt_id": str(handle.attempt_id), "error": error},
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    def _record_evidence(self, handle: WorkspaceHandle, purpose: str) -> ArtifactRef:
        payload = json.dumps(
            {
                "attempt_id": str(handle.attempt_id),
                "provider": handle.provider_name,
                "purpose": purpose,
                "revision": handle.frozen_revision,
                "source_content_sha256": handle.source_content_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
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
        payload = json.dumps(
            {
                "attempt_id": str(record.attempt_id),
                "provider": record.provider_name,
                "succeeded": record.succeeded,
                "quarantined": record.quarantined,
                "already_clean": record.already_clean,
                "steps": record.steps,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
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
    def _clear_readonly_and_retry(
        operation: Callable[[str], None], path: str, exception_info: tuple[object, object, object]
    ) -> None:
        del exception_info
        os.chmod(path, stat.S_IWRITE)
        operation(path)

    def _materialize(self, handle: WorkspaceHandle, spec: WorkspaceSpec) -> None:
        raise NotImplementedError

    def _remove_workspace(self, handle: WorkspaceHandle) -> None:
        raise NotImplementedError
