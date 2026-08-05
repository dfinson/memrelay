from __future__ import annotations

import ast
import asyncio
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.adapters.workspace.base import (
    WorkspaceProviderError,
    WorkspaceSpec,
)
from memrelay_eval.adapters.workspace.clone import IsolatedCloneWorkspaceProvider
from memrelay_eval.adapters.workspace.worktree import TemporaryWorktreeWorkspaceProvider
from memrelay_eval.domain.ids import AttemptId, RunId


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def spec(tmp_path: Path) -> WorkspaceSpec:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "workspace-tests@example.invalid")
    _git(source, "config", "user.name", "Workspace Tests")
    (source / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    _git(source, "add", "fixture.txt")
    _git(source, "commit", "-m", "initial source")
    revision = _git(source, "rev-parse", "HEAD")
    content_hash = sha256(
        subprocess.run(
            ["git", "-C", str(source), "archive", "--format=tar", revision],
            check=True,
            capture_output=True,
        ).stdout
    ).hexdigest()
    return WorkspaceSpec(
        attempt_id=AttemptId.new(),
        run_id=RunId.new(),
        source_root=source,
        frozen_revision=revision,
        source_content_sha256=content_hash,
        allocation_root=tmp_path / "attempts",
    )


@pytest.mark.parametrize(
    "provider_type", [TemporaryWorktreeWorkspaceProvider, IsolatedCloneWorkspaceProvider]
)
def test_destroy_is_idempotent_and_records_each_attempt(provider_type, spec: WorkspaceSpec) -> None:
    provider = provider_type()
    handle = asyncio.run(provider.create(spec))

    first = asyncio.run(provider.destroy(handle))
    second = asyncio.run(provider.destroy(handle))

    assert first.succeeded is True
    assert second.succeeded is True
    assert second.already_clean is True
    assert len(provider.cleanup_records) == 2
    assert len(provider.ledger.artifact_links) >= 3


@pytest.mark.parametrize(
    "provider_type", [TemporaryWorktreeWorkspaceProvider, IsolatedCloneWorkspaceProvider]
)
def test_failed_cleanup_is_quarantined_and_never_reused(provider_type, spec: WorkspaceSpec) -> None:
    def fail_once(step: str) -> None:
        if step == "cleanup_before_remove":
            raise RuntimeError("injected cleanup failure")

    provider = provider_type(fault_injector=fail_once)
    handle = asyncio.run(provider.create(spec))

    cleanup = asyncio.run(provider.destroy(handle))

    assert cleanup.succeeded is False
    assert cleanup.quarantined is True
    assert handle.attempt_root.exists()
    with pytest.raises(WorkspaceProviderError):
        asyncio.run(provider.create(spec))
    assert provider.cleanup_records[-1] == cleanup


@pytest.mark.parametrize(
    "provider_type", [TemporaryWorktreeWorkspaceProvider, IsolatedCloneWorkspaceProvider]
)
def test_create_failure_compensates_and_preserves_cleanup_evidence(
    provider_type, spec: WorkspaceSpec
) -> None:
    def fail_create(step: str) -> None:
        if step == "create_after_materialize":
            raise RuntimeError("injected create failure")

    provider = provider_type(fault_injector=fail_create)

    with pytest.raises(WorkspaceProviderError):
        asyncio.run(provider.create(spec))

    assert provider.cleanup_records
    assert provider.cleanup_records[-1].succeeded is True
    assert provider.ledger.artifact_links


def test_partial_root_creation_is_compensated_before_exposure(spec: WorkspaceSpec) -> None:
    class PartiallyFailingProvider(IsolatedCloneWorkspaceProvider):
        def _make_private_directories(self, handle) -> None:
            super()._make_private_directories(handle)
            raise RuntimeError("injected child-root allocation failure")

    provider = PartiallyFailingProvider()

    with pytest.raises(WorkspaceProviderError):
        asyncio.run(provider.create(spec))

    assert not (spec.allocation_root / str(spec.attempt_id)).exists()
    assert provider.cleanup_records[-1].succeeded is True
    assert provider.cleanup_records[-1].already_clean is False


def test_workspace_layer_has_no_paid_runtime_imports() -> None:
    root = Path(__file__).parents[3] / "src" / "memrelay_eval" / "adapters" / "workspace"
    prohibited = ("copilot", "openai", "inspect", "memrelay")
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports = [
            alias.name.split(".")[0].lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        imports.extend(
            node.module.split(".")[0].lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
        )
        assert not any(name in prohibited for name in imports)
