"""Temporary local Git worktree workspace provider."""

from __future__ import annotations

import subprocess

from .base import BaseWorkspaceProvider, WorkspaceHandle, WorkspaceProviderError, WorkspaceSpec


class TemporaryWorktreeWorkspaceProvider(BaseWorkspaceProvider):
    """Materialize each attempt as a detached worktree from a frozen local revision."""

    provider_name = "temporary_worktree"

    def _materialize(self, handle: WorkspaceHandle, spec: WorkspaceSpec) -> None:
        if handle.private_git_dir is None:
            raise WorkspaceProviderError("worktree provider requires private Git metadata")
        self._assert_attempt_child(handle.private_git_dir, handle.attempt_root, "private Git root")
        self._assert_attempt_child(handle.workspace_root, handle.attempt_root, "workspace root")
        try:
            subprocess.run(
                [
                    "git",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "credential.useHttpPath=false",
                    "-c",
                    "protocol.allow=never",
                    "-c",
                    "protocol.file.allow=always",
                    "clone",
                    "--bare",
                    "--no-local",
                    str(spec.source_root.resolve()),
                    str(handle.private_git_dir),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self._assert_safe_authority_path(handle.private_git_dir, "private Git root")
            subprocess.run(
                [
                    "git",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "credential.useHttpPath=false",
                    "--git-dir",
                    str(handle.private_git_dir),
                    "remote",
                    "remove",
                    "origin",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "credential.useHttpPath=false",
                    "-c",
                    "protocol.allow=never",
                    "-c",
                    "protocol.file.allow=never",
                    "-c",
                    "submodule.recurse=false",
                    "--git-dir",
                    str(handle.private_git_dir),
                    "worktree",
                    "add",
                    "--detach",
                    str(handle.workspace_root),
                    spec.frozen_revision,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self._assert_safe_authority_path(handle.workspace_root, "workspace root")
        except subprocess.CalledProcessError as error:
            raise WorkspaceProviderError(
                error.stderr.strip() or "worktree creation failed"
            ) from error

    def _remove_workspace(self, handle: WorkspaceHandle) -> None:
        if not handle.workspace_root.exists():
            return
        if handle.private_git_dir is None:
            raise WorkspaceProviderError("worktree provider requires private Git metadata")
        self._assert_attempt_child(handle.private_git_dir, handle.attempt_root, "private Git root")
        self._assert_attempt_child(handle.workspace_root, handle.attempt_root, "workspace root")
        try:
            subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(handle.private_git_dir),
                    "worktree",
                    "remove",
                    "--force",
                    str(handle.workspace_root),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            raise WorkspaceProviderError(
                error.stderr.strip() or "worktree removal failed"
            ) from error
