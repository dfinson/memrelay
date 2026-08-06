from __future__ import annotations

import ast
import asyncio
import multiprocessing
import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.adapters.workspace.base import (
    WorkspacePathSafetyError,
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


def _plant_reparse_directory(link: Path, target: Path, kind: str) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction regression requires Windows reparse-point support")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(f"Windows junction capability unavailable: {result.stderr.strip()}")
        is_junction = getattr(link, "is_junction", lambda: False)
        assert is_junction(), "mklink /J did not create a detectable junction"
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink capability unavailable: {error}")
    assert link.is_symlink()


def _cross_process_create(
    source_root: str,
    revision: str,
    content_hash: str,
    allocation_root: str,
    attempt_id: str,
    run_id: str,
    results,
) -> None:
    spec = WorkspaceSpec(
        attempt_id=AttemptId(attempt_id),
        run_id=RunId(run_id),
        source_root=Path(source_root),
        frozen_revision=revision,
        source_content_sha256=content_hash,
        allocation_root=Path(allocation_root),
    )
    try:
        provider = IsolatedCloneWorkspaceProvider()
        handle = asyncio.run(provider.create(spec))
        asyncio.run(provider.destroy(handle))
    except WorkspaceProviderError as error:
        results.put(("rejected", type(error).__name__))
    else:
        results.put(("created", str(handle.attempt_root)))


def _remove_readonly_tree(path: Path) -> None:
    def clear_readonly(operation, target: str, exception_info) -> None:
        del exception_info
        os.chmod(target, 0o666)
        operation(target)

    shutil.rmtree(path, onerror=clear_readonly)


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
    retry = asyncio.run(provider.destroy(handle))
    assert retry.succeeded is False
    assert retry.quarantined is True


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


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_ownership_registry_reparse_path_fails_closed(
    spec: WorkspaceSpec, tmp_path: Path, kind: str
) -> None:
    outside = tmp_path / "outside-ownership"
    registry = spec.allocation_root / ".workspace-ownership"
    _plant_reparse_directory(registry, outside, kind)

    with pytest.raises(WorkspacePathSafetyError):
        asyncio.run(IsolatedCloneWorkspaceProvider().create(spec))

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_quarantine_registry_reparse_path_fails_closed(
    spec: WorkspaceSpec, tmp_path: Path, kind: str
) -> None:
    def fail_cleanup(step: str) -> None:
        if step == "cleanup_before_remove":
            raise RuntimeError("injected cleanup failure")

    provider = IsolatedCloneWorkspaceProvider(fault_injector=fail_cleanup)
    handle = asyncio.run(provider.create(spec))
    outside = tmp_path / "outside-quarantine"
    registry = spec.allocation_root / ".workspace-quarantine"
    _plant_reparse_directory(registry, outside, kind)

    cleanup = asyncio.run(provider.destroy(handle))

    assert cleanup.succeeded is False
    assert cleanup.quarantined is False
    assert "quarantine refused" in (cleanup.error or "")
    assert list(outside.iterdir()) == []


def test_cleanup_reparse_attempt_root_fails_closed(spec: WorkspaceSpec, tmp_path: Path) -> None:
    provider = IsolatedCloneWorkspaceProvider()
    handle = asyncio.run(provider.create(spec))
    outside = tmp_path / "outside-cleanup"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("do not remove", encoding="utf-8")
    displaced_root = tmp_path / "displaced-attempt-root"
    handle.attempt_root.rename(displaced_root)
    _plant_reparse_directory(handle.attempt_root, outside, "junction")

    cleanup = asyncio.run(provider.destroy(handle))

    assert cleanup.succeeded is False
    assert sentinel.read_text(encoding="utf-8") == "do not remove"
    _remove_readonly_tree(displaced_root)


def test_cleanup_dangling_junction_fails_closed(spec: WorkspaceSpec, tmp_path: Path) -> None:
    provider = IsolatedCloneWorkspaceProvider()
    handle = asyncio.run(provider.create(spec))
    displaced_root = tmp_path / "displaced-dangling-attempt-root"
    handle.attempt_root.rename(displaced_root)
    missing_target = tmp_path / "missing-junction-target"
    _plant_reparse_directory(handle.attempt_root, missing_target, "junction")
    missing_target.rmdir()

    cleanup = asyncio.run(provider.destroy(handle))

    assert cleanup.succeeded is False
    assert cleanup.already_clean is False
    os.rmdir(handle.attempt_root)
    _remove_readonly_tree(displaced_root)


def test_cleanup_rejects_nested_junction_without_touching_target(
    spec: WorkspaceSpec, tmp_path: Path
) -> None:
    provider = IsolatedCloneWorkspaceProvider()
    handle = asyncio.run(provider.create(spec))
    outside = tmp_path / "outside-nested-cleanup"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("do not remove", encoding="utf-8")
    junction = handle.cache_root / "escape"
    _plant_reparse_directory(junction, outside, "junction")

    cleanup = asyncio.run(provider.destroy(handle))

    assert cleanup.succeeded is False
    assert sentinel.read_text(encoding="utf-8") == "do not remove"
    os.rmdir(junction)
    retry = asyncio.run(provider.destroy(handle))
    assert retry.succeeded is True


def test_cross_process_claim_allows_one_owner_or_typed_contention(spec: WorkspaceSpec) -> None:
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    arguments = (
        str(spec.source_root),
        spec.frozen_revision,
        spec.source_content_sha256,
        str(spec.allocation_root),
        str(spec.attempt_id),
        str(spec.run_id),
        results,
    )
    processes = [context.Process(target=_cross_process_create, args=arguments) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    outcomes = [results.get(timeout=10) for _ in processes]

    assert [outcome[0] for outcome in outcomes].count("created") == 1
    assert [outcome[0] for outcome in outcomes].count("rejected") == 1
    shutil.rmtree(spec.allocation_root)


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
