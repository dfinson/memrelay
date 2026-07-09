"""Structural guard for the single-writer invariant (E6-S2, SPEC §6.5).

The daemon is the *sole* owner of graph state; the MCP server must reach it only
through the socket. This test parses every module under ``memrelay.mcp`` and
asserts none of them import the graph engine, Ladybug/Graphiti, or the daemon's
server/protocol/lifecycle internals — the shared wire layer
(``memrelay.daemon.transport``) is the only permitted daemon-package dependency.
"""

from __future__ import annotations

import ast
from pathlib import Path

import memrelay.mcp as mcp_pkg

FORBIDDEN_PREFIXES = (
    "ladybug",
    "graphiti",
    "graphiti_core",
    "memrelay.engine",
    "memrelay.daemon.server",
    "memrelay.daemon.protocol",
    "memrelay.daemon.lifecycle",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def _is_forbidden(module: str) -> bool:
    return any(module == p or module.startswith(p + ".") for p in FORBIDDEN_PREFIXES)


def test_mcp_never_imports_graph_or_daemon_internals() -> None:
    pkg_dir = Path(mcp_pkg.__file__).resolve().parent
    offenders: dict[str, list[str]] = {}
    for source in sorted(pkg_dir.glob("*.py")):
        bad = sorted(m for m in _imported_modules(source) if _is_forbidden(m))
        if bad:
            offenders[source.name] = bad
    assert not offenders, (
        "mcp/ must reach the daemon only via memrelay.daemon.transport; "
        f"found forbidden imports: {offenders}"
    )


def test_client_depends_on_the_shared_transport() -> None:
    # Positive control: the client legitimately uses the shared wire layer.
    from memrelay.mcp import client

    modules = _imported_modules(Path(client.__file__))
    assert "memrelay.daemon.transport" in modules
