from __future__ import annotations

import asyncio
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
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


def test_materializers_disable_recursive_submodules_and_file_transport() -> None:
    adapter_root = Path(__file__).parents[3] / "src" / "memrelay_eval" / "adapters" / "workspace"
    clone_source = (adapter_root / "clone.py").read_text(encoding="utf-8")
    worktree_source = (adapter_root / "worktree.py").read_text(encoding="utf-8")

    assert "--no-recurse-submodules" in clone_source
    assert "submodule.recurse=false" in clone_source
    assert "submodule.recurse=false" in worktree_source
    assert "protocol.file.allow=never" in clone_source
    assert "protocol.file.allow=never" in worktree_source
