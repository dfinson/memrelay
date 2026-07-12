"""Unit tests for ``memory_recall``'s ``prefer_repo`` auto-default (E8-S5, #57).

The repo-boost tiebreaker itself -- a *stable* sort that prefers the current repo on score
ties, with no score mutation -- lives in the engine and is covered by
``tests/unit/test_repo_boost.py``. This module pins the complementary *tool-surface* half:
``memory_recall`` must default the engine's ``prefer_repo`` to the caller's resolved current
repo, so a normal recall prefers current-repo results out of the box (the #57 user story),
while an explicit caller value still wins and a no-git session stays byte-identical to the
pre-#57 behavior.

Hermetic: a recording stand-in for :class:`~memrelay.mcp.client.DaemonClient` captures the
exact ``search`` args, and the tool is driven through the real FastMCP ``call_tool`` seam
with an injected ``context_resolver`` -- no daemon, socket, or git subprocess is touched.
"""

from __future__ import annotations

import asyncio
from typing import Any

from memrelay.mcp.server import build_mcp_server


class RecordingClient:
    """Duck-typed :class:`~memrelay.mcp.client.DaemonClient` that records ``search`` args.

    ``build_mcp_server`` only *calls* methods on its client when a tool runs, and these
    tests exercise ``memory_recall`` alone, so recording ``search`` is sufficient. The
    returned shape is the minimal daemon response that ``format_as_map`` accepts.
    """

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str, str | None]] = []

    async def search(
        self, query: str, namespace: str, prefer_repo: str | None = None
    ) -> dict[str, Any]:
        self.search_calls.append((query, namespace, prefer_repo))
        return {"nodes": [], "edges": [], "scores": []}


def _recall(context_resolver: Any, tool_args: dict[str, Any]) -> RecordingClient:
    """Drive ``memory_recall`` once through the real tool with an injected resolver."""
    client = RecordingClient()
    mcp = build_mcp_server(client, context_resolver=context_resolver)
    asyncio.run(mcp.call_tool("memory_recall", tool_args))
    return client


def test_recall_defaults_prefer_repo_to_resolved_current_repo() -> None:
    """(a) With no caller ``prefer_repo``, recall forwards the resolved current repo.

    This is #57's core deliverable: out of the box the engine receives the current repo as
    its tiebreaker, so current-repo results surface first on score ties.
    """
    client = _recall(lambda: ("ns", "owner/repo"), {"query": "auth flow"})

    assert client.search_calls == [("auth flow", "ns", "owner/repo")]


def test_explicit_prefer_repo_overrides_auto_default() -> None:
    """(b) An explicit caller ``prefer_repo`` still wins over the resolved current repo."""
    client = _recall(
        lambda: ("ns", "owner/repo"),
        {"query": "auth flow", "prefer_repo": "explicit/override"},
    )

    assert client.search_calls == [("auth flow", "ns", "explicit/override")]


def test_recall_forwards_none_when_no_git_repo() -> None:
    """(c) No git repo (resolved repo ``None``) + no caller value -> forwards ``None``.

    Byte-identical to the pre-#57 default: the engine's ``if prefer_repo:`` gate stays off,
    so recall is unchanged outside a git repo.
    """
    client = _recall(lambda: ("ns", None), {"query": "auth flow"})

    assert client.search_calls == [("auth flow", "ns", None)]
