"""Host-native, treatment-neutral workspace providers."""

from .base import CleanupRecord, WorkspaceHandle, WorkspaceSnapshot, WorkspaceSpec
from .clone import IsolatedCloneWorkspaceProvider
from .worktree import TemporaryWorktreeWorkspaceProvider

__all__ = [
    "CleanupRecord",
    "IsolatedCloneWorkspaceProvider",
    "TemporaryWorktreeWorkspaceProvider",
    "WorkspaceHandle",
    "WorkspaceSnapshot",
    "WorkspaceSpec",
]
