"""Fault coverage for disposable process cleanup."""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from memrelay_eval.adapters.process import launcher as launcher_module
from memrelay_eval.adapters.process.environment import ProcessRole
from memrelay_eval.adapters.process.launcher import (
    DisposableProcessLauncher,
    LaunchedProcess,
    ProcessLaunchRequest,
    ProcessStartRecord,
)
from memrelay_eval.domain.errors import ProcessLaunchError, ProcessReuseError


def _request(
    tmp_path: Path, attempt_id: str, *, timeout_seconds: float = 10
) -> ProcessLaunchRequest:
    socket_path = tmp_path / f"{attempt_id}.sock"
    socket_path.write_text("owned", encoding="utf-8")
    return ProcessLaunchRequest(
        attempt_id=attempt_id,
        role=ProcessRole.COPILOT_WORKER,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        cwd=tmp_path,
        environment={"PATH": str(Path(sys.executable).parent)},
        timeout_seconds=timeout_seconds,
        socket_paths=(socket_path,),
    )


def test_timeout_stops_owned_process_tree_and_retains_cleanup_record(tmp_path: Path) -> None:
    launcher = DisposableProcessLauncher()
    request = _request(tmp_path, "attempt-timeout", timeout_seconds=0.1)

    report = launcher.execute(request)

    assert report.exit.outcome == "timed_out"
    assert report.cleanup.process_tree_stopped is True
    assert report.cleanup.socket_paths_removed == 1
    assert len(launcher.cleanup_records) == 1


def test_cancellation_compensates_and_role_attempt_pair_cannot_be_reused(tmp_path: Path) -> None:
    launcher = DisposableProcessLauncher()
    request = _request(tmp_path, "attempt-cancel")
    launched = launcher.start(request)

    report = launcher.cancel(launched)

    assert report.exit.outcome == "cancelled"
    assert report.cleanup.process_tree_stopped is True
    with pytest.raises(ProcessReuseError):
        launcher.start(request)


def test_failed_start_consumes_role_attempt_without_recording_secret_material(
    tmp_path: Path,
) -> None:
    launcher = DisposableProcessLauncher()
    socket_path = tmp_path / "attempt-start-failure.sock"
    socket_path.write_text("owned", encoding="utf-8")
    request = ProcessLaunchRequest(
        attempt_id="attempt-start-failure",
        role=ProcessRole.GRADER,
        command=("definitely-missing-executable",),
        cwd=tmp_path,
        environment={"PATH": "safe-path"},
        timeout_seconds=1,
        socket_paths=(socket_path,),
    )

    with pytest.raises(ProcessLaunchError):
        launcher.start(request)
    cleanup = launcher.cleanup_records[-1]
    assert cleanup.reason == "start_failed"
    assert cleanup.socket_paths_removed == 1
    assert cleanup.errors == ()
    assert not socket_path.exists()
    with pytest.raises(ProcessReuseError):
        launcher.start(request)


def test_start_failure_retains_socket_cleanup_failure_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = DisposableProcessLauncher()
    request = ProcessLaunchRequest(
        attempt_id="attempt-start-cleanup-failure",
        role=ProcessRole.GRADER,
        command=("definitely-missing-executable",),
        cwd=tmp_path,
        environment={"PATH": "safe-path"},
        timeout_seconds=1,
        socket_paths=(tmp_path / "owned.sock",),
    )
    monkeypatch.setattr(
        launcher_module,
        "remove_owned_sockets",
        lambda paths: (0, ("socket_remove_failed",)),
    )

    with pytest.raises(ProcessLaunchError):
        launcher.start(request)

    cleanup = launcher.cleanup_records[-1]
    assert cleanup.socket_paths_removed == 0
    assert cleanup.errors == ("socket_remove_failed",)


class _TimeoutProcess:
    returncode: int | None = None

    def __init__(self, wait_started: threading.Event, release_wait: threading.Event) -> None:
        self._wait_started = wait_started
        self._release_wait = release_wait

    def wait(self, timeout: float) -> int:
        del timeout
        self._wait_started.set()
        self._release_wait.wait(timeout=10)
        raise subprocess.TimeoutExpired("inert-child", 0)


def test_wait_cancel_race_records_one_immutable_terminal_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = DisposableProcessLauncher()
    request = _request(tmp_path, "attempt-finalization-race")
    wait_started = threading.Event()
    release_wait = threading.Event()
    launched = LaunchedProcess(
        request,
        _TimeoutProcess(wait_started, release_wait),  # type: ignore[arg-type]
        ProcessStartRecord(request.attempt_id, request.role, 1, datetime.now(UTC), True),
    )
    terminations = 0
    socket_cleanups = 0

    def terminate_once(process):
        nonlocal terminations
        del process
        terminations += 1
        return True, ()

    def remove_once(paths):
        nonlocal socket_cleanups
        del paths
        socket_cleanups += 1
        return 1, ()

    monkeypatch.setattr(launcher_module, "terminate_process_tree", terminate_once)
    monkeypatch.setattr(launcher_module, "remove_owned_sockets", remove_once)
    results = []
    waiter = threading.Thread(target=lambda: results.append(launcher.wait(launched)))
    canceller = threading.Thread(target=lambda: results.append(launcher.cancel(launched)))

    waiter.start()
    assert wait_started.wait(timeout=10)
    canceller.start()
    release_wait.set()
    waiter.join(timeout=10)
    canceller.join(timeout=10)

    assert not waiter.is_alive()
    assert not canceller.is_alive()
    assert len(results) == 2
    assert results[0] is results[1]
    assert len(launcher.cleanup_records) == 1
    assert terminations == 1
    assert socket_cleanups == 1
