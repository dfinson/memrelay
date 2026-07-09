"""Root-cause guard for issue #94: the git probe must detach its stdin.

``current_repo()`` shells out to ``git remote get-url origin``. On Windows the
MCP server runs on **stdio**, so its own stdin is the read end of the long-lived
agent->server pipe. If the ``git`` child inherits that handle, ``subprocess.run``
wedges (ignoring its ``timeout=5``) until the agent connection closes — hanging
every memory tool call. Passing ``stdin=subprocess.DEVNULL`` is the fix; this
fast, cross-platform test pins the kwarg so it can't be "cleaned up" back into a
hang. The behavioral end-to-end proof lives in
``tests/integration/test_mcp_stdio_roundtrip.py``.
"""

from __future__ import annotations

import subprocess

from memrelay.mcp import namespace


def test_current_repo_detaches_git_stdin(monkeypatch) -> None:
    """``current_repo`` must invoke git with ``stdin=subprocess.DEVNULL`` (issue #94)."""
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            cmd, 0, stdout="git@github.com:dfinson/memrelay.git\n", stderr=""
        )

    monkeypatch.setattr(namespace.subprocess, "run", fake_run)

    repo = namespace.current_repo()

    # Behavior is unchanged: the SSH remote still parses to owner/name.
    assert repo == "dfinson/memrelay"

    kwargs = captured["kwargs"]
    assert captured["cmd"] == ["git", "remote", "get-url", "origin"]
    # The fix: stdin is detached from the (stdio) MCP server's own stdin pipe.
    assert kwargs.get("stdin") is subprocess.DEVNULL
    # The rest of the call shape must survive alongside the fix.
    assert kwargs.get("capture_output") is True
    assert kwargs.get("text") is True
    assert kwargs.get("timeout") == 5
