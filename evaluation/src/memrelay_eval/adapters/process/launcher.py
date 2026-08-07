"""Disposable local process launcher with attempt-scoped process-tree ownership."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from ...domain.errors import ProcessLaunchError, ProcessReuseError
from .cleanup import remove_owned_sockets, terminate_process_tree
from .environment import ProcessRole


@dataclass(frozen=True, slots=True)
class ProcessLaunchRequest:
    """All non-secret material required to start one role once for one attempt."""

    attempt_id: str
    role: ProcessRole
    command: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]
    timeout_seconds: float
    socket_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not self.attempt_id or not self.command or self.timeout_seconds <= 0:
            raise ProcessLaunchError("process_launch_request_invalid")
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(self, "environment", dict(self.environment))
        object.__setattr__(self, "socket_paths", tuple(self.socket_paths))


@dataclass(frozen=True, slots=True)
class ProcessStartRecord:
    attempt_id: str
    role: ProcessRole
    pid: int
    occurred_at: datetime
    isolated_process_group: bool


@dataclass(frozen=True, slots=True)
class ProcessExitRecord:
    attempt_id: str
    role: ProcessRole
    returncode: int | None
    outcome: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProcessCleanupRecord:
    attempt_id: str
    role: ProcessRole
    reason: str
    occurred_at: datetime
    process_tree_stopped: bool
    socket_paths_removed: int
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcessRunReport:
    """Typed terminal records without command, path, environment, or output capture."""

    start: ProcessStartRecord
    exit: ProcessExitRecord
    cleanup: ProcessCleanupRecord


@dataclass(slots=True)
class LaunchedProcess:
    request: ProcessLaunchRequest
    process: subprocess.Popen[bytes]
    start: ProcessStartRecord
    report: ProcessRunReport | None = None
    finalization_lock: Lock = field(default_factory=Lock, repr=False)


class DisposableProcessLauncher:
    """Launchs a fresh process group per role/attempt and retains terminal evidence."""

    def __init__(self) -> None:
        self._claimed: set[tuple[str, ProcessRole]] = set()
        self._cleanup_records: list[ProcessCleanupRecord] = []
        self._lock = Lock()

    @property
    def cleanup_records(self) -> tuple[ProcessCleanupRecord, ...]:
        with self._lock:
            return tuple(self._cleanup_records)

    def start(self, request: ProcessLaunchRequest) -> LaunchedProcess:
        key = (request.attempt_id, request.role)
        with self._lock:
            if key in self._claimed:
                raise ProcessReuseError("process_role_attempt_already_consumed")
            self._claimed.add(key)
        try:
            process = subprocess.Popen(
                request.command,
                cwd=request.cwd,
                env=dict(request.environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **_process_group_options(),
            )
        except OSError as error:
            removed, socket_errors = remove_owned_sockets(request.socket_paths)
            self._append_cleanup_record(
                ProcessCleanupRecord(
                    request.attempt_id,
                    request.role,
                    "start_failed",
                    datetime.now(UTC),
                    False,
                    removed,
                    socket_errors,
                )
            )
            raise ProcessLaunchError("process_start_failed") from error
        return LaunchedProcess(
            request,
            process,
            ProcessStartRecord(
                request.attempt_id,
                request.role,
                process.pid,
                datetime.now(UTC),
                isolated_process_group=True,
            ),
        )

    def execute(self, request: ProcessLaunchRequest) -> ProcessRunReport:
        return self.wait(self.start(request))

    def wait(self, launched: LaunchedProcess) -> ProcessRunReport:
        try:
            returncode = launched.process.wait(timeout=launched.request.timeout_seconds)
        except subprocess.TimeoutExpired:
            return self._finalize(launched, "timed_out", None, stop_process_tree=True)
        outcome = "exited" if returncode == 0 else "failed"
        return self._finalize(launched, outcome, returncode, stop_process_tree=False)

    def cancel(self, launched: LaunchedProcess) -> ProcessRunReport:
        """Compensate cancellation with owned process-tree and socket cleanup."""
        return self._finalize(launched, "cancelled", None, stop_process_tree=True)

    def _finalize(
        self,
        launched: LaunchedProcess,
        outcome: str,
        returncode: int | None,
        *,
        stop_process_tree: bool,
    ) -> ProcessRunReport:
        with launched.finalization_lock:
            if launched.report is not None:
                return launched.report
            if stop_process_tree:
                process_tree_stopped, process_errors = terminate_process_tree(launched.process)
            else:
                process_tree_stopped, process_errors = False, ()
            removed, socket_errors = remove_owned_sockets(launched.request.socket_paths)
            now = datetime.now(UTC)
            cleanup = ProcessCleanupRecord(
                launched.request.attempt_id,
                launched.request.role,
                outcome,
                now,
                process_tree_stopped,
                removed,
                (*process_errors, *socket_errors),
            )
            self._append_cleanup_record(cleanup)
            report = ProcessRunReport(
                launched.start,
                ProcessExitRecord(
                    launched.request.attempt_id,
                    launched.request.role,
                    launched.process.returncode if returncode is None else returncode,
                    outcome,
                    now,
                ),
                cleanup,
            )
            launched.report = report
            return report

    def _append_cleanup_record(self, record: ProcessCleanupRecord) -> None:
        with self._lock:
            self._cleanup_records.append(record)


def _process_group_options() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}
