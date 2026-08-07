"""Platform-specific process-tree and terminal socket compensations."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CleanupActions:
    """Non-secret facts from best-effort terminal compensations."""

    process_tree_stopped: bool
    socket_paths_removed: int
    errors: tuple[str, ...]


def terminate_process_tree(process: subprocess.Popen[bytes]) -> tuple[bool, tuple[str, ...]]:
    """Stop the owned process group/job without observing the child output."""
    if process.poll() is not None:
        return False, ()
    try:
        if os.name == "nt":
            subprocess.run(
                ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
        return True, ()
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=10)
            return True, ()
        except (OSError, subprocess.TimeoutExpired):
            return False, ("process_tree_stop_failed",)


def remove_owned_sockets(paths: Iterable[Path]) -> tuple[int, tuple[str, ...]]:
    """Remove only explicitly owned terminal socket paths."""
    removed = 0
    errors: list[str] = []
    for path in paths:
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
                removed += 1
        except OSError:
            errors.append("socket_remove_failed")
    return removed, tuple(errors)
