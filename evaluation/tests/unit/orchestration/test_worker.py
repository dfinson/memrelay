from __future__ import annotations

from pathlib import Path

import pytest
from memrelay_eval.adapters.process.environment import (
    CredentialDomain,
    CredentialReference,
    ProcessRole,
)
from memrelay_eval.adapters.process.launcher import ProcessLaunchRequest
from memrelay_eval.adapters.workspace.base import WorkspaceHandle
from memrelay_eval.domain.errors import ProcessEnvironmentError, ProcessWorkerBoundaryError
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.orchestration.limits import AttemptProcessLimiter
from memrelay_eval.orchestration.worker import DisposableAttemptWorker


class _RecordingLauncher:
    def __init__(self) -> None:
        self.requests: list[ProcessLaunchRequest] = []

    def execute(self, request: ProcessLaunchRequest) -> object:
        self.requests.append(request)
        return object()


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceHandle:
    root = tmp_path / "attempt"
    return WorkspaceHandle(
        attempt_id=AttemptId.new(),
        run_id=RunId.new(),
        provider_name="test",
        allocation_root=tmp_path,
        attempt_root=root,
        workspace_root=root / "workspace",
        agent_session_root=root / "agent-session",
        cache_root=root / "cache",
        staging_root=root / "staging",
        telemetry_root=root / "telemetry",
        memrelay_home=root / "memrelay-home",
        graph_root=root / "graph",
        spool_root=root / "spool",
        socket_path=root / "socket" / "attempt.sock",
        config_root=root / "config",
        private_git_dir=None,
        telemetry_identity="telemetry_test",
        frozen_revision="a" * 40,
        source_content_sha256="a" * 64,
        source_root=tmp_path / "source",
    )


@pytest.fixture
def launcher() -> _RecordingLauncher:
    return _RecordingLauncher()


def _worker(launcher: _RecordingLauncher) -> DisposableAttemptWorker:
    return DisposableAttemptWorker(launcher, AttemptProcessLimiter(1))  # type: ignore[arg-type]


def _request(workspace: WorkspaceHandle, **overrides: object) -> ProcessLaunchRequest:
    values: dict[str, object] = {
        "attempt_id": str(workspace.attempt_id),
        "role": ProcessRole.GRADER,
        "command": ("inert-child",),
        "cwd": workspace.workspace_root,
        "environment": {},
        "timeout_seconds": 10,
        "socket_paths": (),
    }
    values.update(overrides)
    return ProcessLaunchRequest(**values)  # type: ignore[arg-type]


def test_worker_rejects_attempt_workspace_and_socket_mismatches(
    workspace: WorkspaceHandle, launcher: _RecordingLauncher, tmp_path: Path
) -> None:
    worker = _worker(launcher)

    with pytest.raises(ProcessWorkerBoundaryError, match="attempt_mismatch"):
        worker.execute(_request(workspace, attempt_id=str(AttemptId.new())), workspace)
    with pytest.raises(ProcessWorkerBoundaryError, match="workspace_mismatch"):
        worker.execute(_request(workspace, cwd=tmp_path / "other-workspace"), workspace)
    with pytest.raises(ProcessWorkerBoundaryError, match="socket_mismatch"):
        worker.execute(_request(workspace, socket_paths=(tmp_path / "other.sock",)), workspace)

    assert launcher.requests == []


def test_worker_rejects_credential_reference_crossing_role_boundary(
    workspace: WorkspaceHandle, launcher: _RecordingLauncher
) -> None:
    worker = _worker(launcher)
    daemon_key = CredentialReference(
        "OPENAI_API_KEY", CredentialDomain.OPENAI, ProcessRole.MEMRELAY_DAEMON
    )

    with pytest.raises(ProcessEnvironmentError, match="credential_cross_boundary_denied"):
        worker.execute(
            _request(workspace, role=ProcessRole.COPILOT_WORKER),
            workspace,
            credential_references=(daemon_key,),
            credential_values={"OPENAI_API_KEY": "synthetic-test-value"},
        )

    assert launcher.requests == []


def test_worker_derives_authoritative_workspace_environment_and_socket(
    workspace: WorkspaceHandle, launcher: _RecordingLauncher
) -> None:
    worker = _worker(launcher)
    result = worker.execute(_request(workspace), workspace)

    assert result is not None
    assert len(launcher.requests) == 1
    launched = launcher.requests[0]
    assert launched.cwd == workspace.workspace_root
    assert launched.environment == workspace.environment
    assert launched.socket_paths == (workspace.socket_path,)
