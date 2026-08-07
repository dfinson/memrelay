"""Fault coverage for disposable process cleanup."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from memrelay_eval.adapters.process.environment import ProcessRole
from memrelay_eval.adapters.process.launcher import DisposableProcessLauncher, ProcessLaunchRequest
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
    request = ProcessLaunchRequest(
        attempt_id="attempt-start-failure",
        role=ProcessRole.GRADER,
        command=("definitely-missing-executable",),
        cwd=tmp_path,
        environment={"PATH": "safe-path"},
        timeout_seconds=1,
    )

    with pytest.raises(ProcessLaunchError):
        launcher.start(request)
    assert launcher.cleanup_records[-1].reason == "start_failed"
    with pytest.raises(ProcessReuseError):
        launcher.start(request)
