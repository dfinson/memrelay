"""Network-free isolated local clone workspace provider."""

from __future__ import annotations

import subprocess

from .base import BaseWorkspaceProvider, WorkspaceHandle, WorkspaceProviderError, WorkspaceSpec


class IsolatedCloneWorkspaceProvider(BaseWorkspaceProvider):
    """Materialize each attempt from a local frozen source without network access."""

    provider_name = "isolated_clone"

    def _materialize(self, handle: WorkspaceHandle, spec: WorkspaceSpec) -> None:
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
                    "--no-local",
                    "--no-checkout",
                    str(spec.source_root.resolve()),
                    str(handle.workspace_root),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self._assert_safe_authority_path(handle.workspace_root, "workspace root")
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
                    "-C",
                    str(handle.workspace_root),
                    "checkout",
                    "--detach",
                    "--no-recurse-submodules",
                    spec.frozen_revision,
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
                    "-C",
                    str(handle.workspace_root),
                    "remote",
                    "remove",
                    "origin",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            raise WorkspaceProviderError(
                error.stderr.strip() or "local clone creation failed"
            ) from error

    def _remove_workspace(self, handle: WorkspaceHandle) -> None:
        # A clone has no source-side worktree registration; the base removes its attempt root.
        del handle
