"""Daemon process ownership contracts for the product treatment."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.adapters.memrelay import (
    MemrelayProductTreatment,
    ProductProvisionRequest,
    build_framework_process_environments,
)
from memrelay_eval.adapters.process.launcher import (
    LaunchedProcess,
    ProcessCleanupRecord,
    ProcessExitRecord,
    ProcessRunReport,
    ProcessStartRecord,
)
from memrelay_eval.domain.errors import ConformancePauseError


class _LiveHealthClient:
    async def health(self) -> dict[str, object]:
        return {
            "status": "running",
            "sessions_observed": 0,
            "active_sessions": 0,
            "episodes_ingested": 0,
            "spool_pending": 0,
            "notes_failed": 0,
            "poison_skipped": 0,
        }


class _Launcher:
    def __init__(self) -> None:
        self.request = None
        self.cancelled = False

    def start(self, request: object) -> LaunchedProcess:
        self.request = request
        start = ProcessStartRecord(request.attempt_id, request.role, 4321, datetime.now(UTC), True)
        return LaunchedProcess(
            request,
            SimpleNamespace(poll=lambda: None, returncode=None),
            start,
        )

    def cancel(self, launched: LaunchedProcess) -> ProcessRunReport:
        self.cancelled = True
        now = datetime.now(UTC)
        return ProcessRunReport(
            launched.start,
            ProcessExitRecord(
                launched.request.attempt_id, launched.request.role, 0, "cancelled", now
            ),
            ProcessCleanupRecord(
                launched.request.attempt_id,
                launched.request.role,
                "cancelled",
                now,
                True,
                1,
                (),
            ),
        )


class _CrashedLauncher(_Launcher):
    def start(self, request: object) -> LaunchedProcess:
        launched = super().start(request)
        launched.process = SimpleNamespace(poll=lambda: 1, returncode=1)
        return launched


def _request(tmp_path: Path, **overrides: object) -> ProductProvisionRequest:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (home / "config.toml").write_text("", encoding="utf-8")
    (home / "spool").mkdir()
    (home / "spool" / "spool.db").write_bytes(b"canonical fake observation")
    daemon, agent, mcp = build_framework_process_environments()
    values: dict[str, object] = {
        "attempt_id": "attempt-contract",
        "home_path": home,
        "workspace_root": workspace,
        "namespace": "namespace",
        "daemon_environment": daemon,
        "agent_environment": agent,
        "mcp_environment": mcp,
    }
    values.update(overrides)
    return ProductProvisionRequest(**values)  # type: ignore[arg-type]


def test_provision_uses_shipped_foreground_serve_and_live_health(tmp_path: Path) -> None:
    launcher = _Launcher()
    treatment = MemrelayProductTreatment(
        artifact_store=InMemoryArtifactStore(),
        launcher=launcher,  # type: ignore[arg-type]
        health_client_factory=lambda _: _LiveHealthClient(),
    )
    handle = asyncio.run(treatment.provision(_request(tmp_path)))

    assert launcher.request.command[-1] == "_serve"
    assert "DaemonServer" not in type(treatment).__module__
    assert handle.agent_mcp.command[-1] == "mcp"
    assert set(handle.agent_mcp.environment).isdisjoint({"OPENAI_API_KEY", "OPENAI_BASE_URL"})
    assert handle.agent_mcp.tool_contract.tool_names == (
        "memory_detail",
        "memory_note",
        "memory_recall",
    )
    refs = asyncio.run(treatment.collect_state(handle))
    assert len(refs) == 1
    asyncio.run(treatment.close(handle))
    assert launcher.cancelled


def test_collect_state_rejects_missing_observation_path(tmp_path: Path) -> None:
    treatment = MemrelayProductTreatment(
        artifact_store=InMemoryArtifactStore(),
        launcher=_Launcher(),  # type: ignore[arg-type]
        health_client_factory=lambda _: _LiveHealthClient(),
    )
    handle = asyncio.run(treatment.provision(_request(tmp_path)))
    handle.paths.observation_path.unlink()

    with pytest.raises(ConformancePauseError, match="canonical observation artifact"):
        asyncio.run(treatment.collect_state(handle))

    asyncio.run(treatment.close(handle))


def test_preflight_failure_does_not_launch_a_process(tmp_path: Path) -> None:
    launcher = _Launcher()
    treatment = MemrelayProductTreatment(
        launcher=launcher,  # type: ignore[arg-type]
        health_client_factory=lambda _: _LiveHealthClient(),
    )
    with pytest.raises(ConformancePauseError):
        asyncio.run(treatment.provision(_request(tmp_path, llm_strategy="azure")))
    assert launcher.request is None


def test_crashed_foreground_daemon_is_cleaned_up_before_exposure(tmp_path: Path) -> None:
    launcher = _CrashedLauncher()
    treatment = MemrelayProductTreatment(
        launcher=launcher,  # type: ignore[arg-type]
        health_client_factory=lambda _: _LiveHealthClient(),
    )
    with pytest.raises(ConformancePauseError):
        asyncio.run(treatment.provision(_request(tmp_path)))
    assert launcher.cancelled
