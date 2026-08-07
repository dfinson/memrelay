from __future__ import annotations

import asyncio
import socket
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.adapters.workspace import clone as clone_module
from memrelay_eval.adapters.workspace import worktree as worktree_module
from memrelay_eval.adapters.workspace.base import (
    WorkspaceCollisionError,
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
def frozen_source(tmp_path: Path) -> tuple[Path, str, str]:
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


@pytest.fixture(params=[TemporaryWorktreeWorkspaceProvider, IsolatedCloneWorkspaceProvider])
def provider(request: pytest.FixtureRequest):
    return request.param()


def _spec(source: Path, revision: str, content_hash: str, root: Path) -> WorkspaceSpec:
    return WorkspaceSpec(
        attempt_id=AttemptId.new(),
        run_id=RunId.new(),
        source_root=source,
        frozen_revision=revision,
        source_content_sha256=content_hash,
        allocation_root=root,
    )


async def _create_pair(provider, first: WorkspaceSpec, second: WorkspaceSpec):
    return await asyncio.gather(provider.create(first), provider.create(second))


def test_providers_provision_identical_isolated_attempt_layout(
    provider, frozen_source: tuple[Path, str, str], tmp_path: Path
) -> None:
    source, revision, content_hash = frozen_source
    handle = asyncio.run(
        provider.create(_spec(source, revision, content_hash, tmp_path / "attempts"))
    )

    assert handle.provider_name in {"temporary_worktree", "isolated_clone"}
    assert handle.workspace_root != source.resolve()
    assert handle.workspace_root.is_relative_to(handle.attempt_root)
    assert all(path.is_relative_to(handle.attempt_root) for path in handle.mutable_roots)
    assert handle.environment["MEMRELAY_HOME"] == str(handle.memrelay_home)
    assert handle.telemetry_identity.startswith("telemetry_")
    assert str(handle.attempt_id) not in handle.telemetry_identity
    assert not any(label in str(handle.attempt_root).lower() for label in ("arm", "treatment"))
    assert (handle.workspace_root / "README.md").read_text(encoding="utf-8") == "frozen source\n"
    assert _git(handle.workspace_root, "rev-parse", "HEAD") == revision

    snapshot = asyncio.run(provider.freeze(handle))
    assert snapshot.revision == revision
    assert snapshot.source_content_sha256 == content_hash
    assert snapshot.workspace_content_sha256 == content_hash
    _git(handle.workspace_root, "config", "--local", "workspace.isolated", "true")
    assert (
        subprocess.run(
            ["git", "-C", str(source), "config", "--local", "--get", "workspace.isolated"],
            capture_output=True,
            text=True,
        ).returncode
        != 0
    )
    cleanup = asyncio.run(provider.destroy(handle))

    assert cleanup.succeeded is True
    assert cleanup.quarantined is False
    assert not handle.attempt_root.exists()
    assert provider.ledger.artifact_links
    assert provider.telemetry.observations


def test_parallel_allocations_do_not_share_writable_attempt_state(
    provider, frozen_source: tuple[Path, str, str], tmp_path: Path
) -> None:
    source, revision, content_hash = frozen_source
    root = tmp_path / "attempts"
    first, second = asyncio.run(
        _create_pair(
            provider,
            _spec(source, revision, content_hash, root),
            _spec(source, revision, content_hash, root),
        )
    )

    assert first.attempt_root != second.attempt_root
    assert set(first.mutable_roots).isdisjoint(second.mutable_roots)
    assert first.agent_session_root != second.agent_session_root
    assert first.cache_root != second.cache_root
    assert first.memrelay_home != second.memrelay_home
    assert first.graph_root != second.graph_root
    assert first.spool_root != second.spool_root
    assert first.socket_path != second.socket_path
    assert first.telemetry_identity != second.telemetry_identity

    asyncio.run(provider.destroy(first))
    asyncio.run(provider.destroy(second))


def test_rejects_attempt_reuse_path_escape_and_dirty_source(
    provider, frozen_source: tuple[Path, str, str], tmp_path: Path
) -> None:
    source, revision, content_hash = frozen_source
    spec = _spec(source, revision, content_hash, tmp_path / "attempts")
    handle = asyncio.run(provider.create(spec))

    with pytest.raises(WorkspaceCollisionError):
        asyncio.run(provider.create(spec))
    asyncio.run(provider.destroy(handle))
    with pytest.raises(WorkspaceCollisionError):
        asyncio.run(provider.create(spec))

    escaped = _spec(source, revision, content_hash, source / "attempts")
    with pytest.raises(WorkspaceCollisionError):
        asyncio.run(provider.create(escaped))

    (source / "dirty.txt").write_text("not frozen\n", encoding="utf-8")
    dirty = _spec(source, revision, content_hash, tmp_path / "other-attempts")
    with pytest.raises(WorkspaceCollisionError):
        asyncio.run(provider.create(dirty))


def test_clone_uses_only_the_frozen_local_source(
    frozen_source: tuple[Path, str, str], tmp_path: Path
) -> None:
    source, revision, content_hash = frozen_source
    provider = IsolatedCloneWorkspaceProvider()
    handle = asyncio.run(
        provider.create(_spec(source, revision, content_hash, tmp_path / "attempts"))
    )

    assert _git(handle.workspace_root, "remote") == ""

    asyncio.run(provider.destroy(handle))


@pytest.mark.parametrize(
    "provider_type", [TemporaryWorktreeWorkspaceProvider, IsolatedCloneWorkspaceProvider]
)
def test_materializers_disable_network_credentials_and_recursive_submodules(
    monkeypatch: pytest.MonkeyPatch,
    frozen_source: tuple[Path, str, str],
    provider_type,
    tmp_path: Path,
) -> None:
    source, revision, content_hash = frozen_source
    captured_commands: list[list[str]] = []
    real_run = subprocess.run

    def guarded_run(command, *args, **kwargs):
        command_parts = [str(part) for part in command]
        captured_commands.append(command_parts)
        assert not any(
            value.startswith(("http://", "https://", "ssh://", "git@")) for value in command_parts
        )
        return real_run(command, *args, **kwargs)

    def blocked_network(*args, **kwargs):
        raise AssertionError(f"workspace materialization attempted network access: {args!r}")

    real_connect = socket.socket.connect

    def block_non_loopback_connect(socket_instance, address):
        if address[0] in {"127.0.0.1", "::1"}:
            return real_connect(socket_instance, address)
        return blocked_network(socket_instance, address)

    monkeypatch.setattr(clone_module.subprocess, "run", guarded_run)
    monkeypatch.setattr(worktree_module.subprocess, "run", guarded_run)
    monkeypatch.setattr(socket, "getaddrinfo", blocked_network)
    monkeypatch.setattr(socket.socket, "connect", block_non_loopback_connect)
    provider = provider_type()

    handle = asyncio.run(
        provider.create(_spec(source, revision, content_hash, tmp_path / "attempts"))
    )

    materialization = [
        command
        for command in captured_commands
        if "clone" in command or "checkout" in command or "worktree" in command
    ]
    assert materialization
    assert all("credential.helper=" in command for command in materialization)
    assert all("credential.useHttpPath=false" in command for command in materialization)
    assert any("protocol.allow=never" in command for command in materialization)
    assert any("protocol.file.allow=never" in command for command in materialization)
    assert any("submodule.recurse=false" in command for command in materialization)
    if provider_type is IsolatedCloneWorkspaceProvider:
        assert any("--no-recurse-submodules" in command for command in materialization)

    asyncio.run(provider.destroy(handle))
