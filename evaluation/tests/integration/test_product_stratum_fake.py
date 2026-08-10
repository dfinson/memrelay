"""Fake-only integration coverage of evaluator-owned product evidence."""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
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
from memrelay_eval.adapters.workspace import TemporaryWorktreeWorkspaceProvider, WorkspaceSpec
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.orchestration.attempt import AttemptTerminalRecorder, ProductTreatmentAttempt


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


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _frozen_source(tmp_path: Path) -> tuple[Path, str, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "workspace-tests@example.invalid")
    _git(source, "config", "user.name", "Workspace Tests")
    (source / "README.md").write_text("frozen source\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "initial source")
    revision = _git(source, "rev-parse", "HEAD")
    content_hash = sha256(
        subprocess.run(
            ["git", "-C", str(source), "archive", "--format=tar", revision],
            check=True,
            capture_output=True,
        ).stdout
    ).hexdigest()
    return source, revision, content_hash


def _workspace_spec(source: Path, revision: str, content_hash: str, root: Path) -> WorkspaceSpec:
    return WorkspaceSpec(
        attempt_id=AttemptId.new(),
        run_id=RunId.new(),
        source_root=source,
        frozen_revision=revision,
        source_content_sha256=content_hash,
        allocation_root=root,
    )


async def _create_pair(
    provider: TemporaryWorktreeWorkspaceProvider,
    first: WorkspaceSpec,
    second: WorkspaceSpec,
):
    return await asyncio.gather(provider.create(first), provider.create(second))


def _request_from_workspace(handle: object, *, attempt_id: str) -> ProductProvisionRequest:
    home = handle.memrelay_home
    (home / "config.toml").write_text("", encoding="utf-8")
    (home / "spool").mkdir(exist_ok=True)
    (home / "spool" / "spool.db").write_bytes(b"canonical fake observation")
    daemon, agent, mcp = build_framework_process_environments()
    return ProductProvisionRequest(
        attempt_id=attempt_id,
        home_path=home,
        workspace_root=handle.workspace_root,
        namespace="namespace",
        daemon_environment=daemon,
        agent_environment=agent,
        mcp_environment=mcp,
    )


def test_fake_product_attempt_preserves_observation_and_cleanup_evidence(tmp_path: Path) -> None:
    launcher = _Launcher()
    store = InMemoryArtifactStore()
    treatment = MemrelayProductTreatment(
        artifact_store=store,
        launcher=launcher,  # type: ignore[arg-type]
        health_client_factory=lambda _: _LiveHealthClient(),
    )
    handle = asyncio.run(treatment.provision(_request(tmp_path, attempt_id="attempt-integration")))
    refs = asyncio.run(treatment.collect_state(handle))
    state = json.loads(store.open_verified(refs[0]))
    asyncio.run(treatment.close(handle))
    cleanup = json.loads(store.open_verified(handle.evidence_refs[-1]))

    assert state["observation_artifact_exists"] is True
    assert Path(state["observation_path"]).is_file()
    assert cleanup["process"]["process_tree_stopped"] is True


def test_fake_product_attempt_rejects_fourth_tool_before_evidence(tmp_path: Path) -> None:
    async def four_tools() -> tuple[str, ...]:
        return ("memory_recall", "memory_detail", "memory_note", "spoof")

    treatment = MemrelayProductTreatment(
        launcher=_Launcher(),  # type: ignore[arg-type]
        health_client_factory=lambda _: _LiveHealthClient(),
    )
    handle = asyncio.run(treatment.provision(_request(tmp_path, mcp_tool_surface_probe=four_tools)))
    with pytest.raises(ConformancePauseError):
        asyncio.run(treatment.collect_state(handle))
    asyncio.run(treatment.close(handle))


def test_product_attempt_uses_existing_ledger_claim_and_telemetry(tmp_path: Path) -> None:
    ledger = InMemoryLedger()
    telemetry = InMemoryTelemetry()
    treatment = MemrelayProductTreatment(
        launcher=_Launcher(),  # type: ignore[arg-type]
        health_client_factory=lambda _: _LiveHealthClient(),
    )
    stage = ProductTreatmentAttempt(
        treatment,
        AttemptTerminalRecorder(ledger, telemetry),
        telemetry,
    )
    handle = asyncio.run(
        stage.provision(_request(tmp_path), attempt_id=AttemptId.new(), run_id=RunId.new())
    )
    references = asyncio.run(stage.collect_and_close(handle))

    assert references
    assert telemetry.observations[0].event_name == "attempt_started"


def test_parallel_product_attempts_keep_attempt_scoped_paths_distinct(tmp_path: Path) -> None:
    source, revision, content_hash = _frozen_source(tmp_path)
    provider = TemporaryWorktreeWorkspaceProvider()
    first_workspace, second_workspace = asyncio.run(
        _create_pair(
            provider,
            _workspace_spec(source, revision, content_hash, tmp_path / "attempts"),
            _workspace_spec(source, revision, content_hash, tmp_path / "attempts"),
        )
    )
    try:
        assert first_workspace.memrelay_home != second_workspace.memrelay_home
        assert first_workspace.graph_root != second_workspace.graph_root
        assert first_workspace.spool_root != second_workspace.spool_root
        assert first_workspace.socket_path != second_workspace.socket_path

        first_treatment = MemrelayProductTreatment(
            launcher=_Launcher(),  # type: ignore[arg-type]
            health_client_factory=lambda _: _LiveHealthClient(),
        )
        second_treatment = MemrelayProductTreatment(
            launcher=_Launcher(),  # type: ignore[arg-type]
            health_client_factory=lambda _: _LiveHealthClient(),
        )
        first_handle = asyncio.run(
            first_treatment.provision(
                _request_from_workspace(first_workspace, attempt_id="attempt-a")
            )
        )
        second_handle = asyncio.run(
            second_treatment.provision(
                _request_from_workspace(second_workspace, attempt_id="attempt-b")
            )
        )

        assert first_handle.paths.home_path != second_handle.paths.home_path
        assert first_handle.paths.observation_path != second_handle.paths.observation_path
        assert first_handle.paths.endpoint != second_handle.paths.endpoint
        asyncio.run(first_treatment.close(first_handle))
        asyncio.run(second_treatment.close(second_handle))
    finally:
        asyncio.run(provider.destroy(first_workspace))
        asyncio.run(provider.destroy(second_workspace))
