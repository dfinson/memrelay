"""Render daemon results into agent-facing text (SPEC §4.3/§4.4, ``mcp/format.py``).

The walking skeleton keeps formatting deliberately simple: a compact, readable
"memory map" for recall and a flat detail view. Rich graph-as-map / Mermaid
rendering (SPEC §4.3) is a later-wave concern; the shapes consumed here match the
daemon wire schema so they will not change when the real engine lands.
"""

from __future__ import annotations

from typing import Any

_NO_RESULTS = "No relevant memories found."


def format_as_map(results: dict[str, Any]) -> str:
    """Format a ``search`` response ``{"nodes", "edges", "scores"}`` as a map."""
    nodes = results.get("nodes") or []
    edges = results.get("edges") or []
    if not nodes:
        return _NO_RESULTS

    scores = results.get("scores") or []
    lines = ["## Memory Map", "", "### Entities"]
    for index, node in enumerate(nodes):
        score = scores[index] if index < len(scores) else None
        lines.append(_format_node_line(node, score))

    if edges:
        lines += ["", "### Relationships"]
        lines += [_format_edge_line(edge) for edge in edges]

    return "\n".join(lines)


def format_detail(result: dict[str, Any]) -> str:
    """Format a ``detail`` response ``{"node", "connected_edges", "episodes"}``."""
    node = result.get("node") or {}
    if not node:
        return "Entity not found."

    name = node.get("name", "(unnamed)")
    uuid = node.get("uuid", "?")
    lines = [f"# {name}", f"`{uuid}`"]
    if node.get("summary"):
        lines += ["", node["summary"]]

    edges = result.get("connected_edges") or []
    if edges:
        lines += ["", "### Connections"]
        lines += [_format_edge_line(edge) for edge in edges]

    episodes = result.get("episodes") or []
    if episodes:
        lines += ["", "### Episodes"]
        for episode in episodes:
            label = episode.get("name", "episode")
            content = episode.get("content", "")
            lines.append(f"- **{label}**: {content}".rstrip())

    return "\n".join(lines)


def _format_node_line(node: dict[str, Any], score: float | None) -> str:
    uuid = node.get("uuid", "?")
    name = node.get("name", "(unnamed)")
    suffix = f" _(score {score:.2f})_" if isinstance(score, int | float) else ""
    line = f"- **{name}** `{uuid}`{suffix}"
    summary = node.get("summary")
    if summary:
        line += f" - {summary}"
    return line


def _format_edge_line(edge: dict[str, Any]) -> str:
    name = edge.get("name", "RELATED_TO")
    source = edge.get("source_node_uuid", "?")
    target = edge.get("target_node_uuid", "?")
    fact = edge.get("fact")
    line = f"- {source} -[{name}]-> {target}"
    if fact:
        line += f": {fact}"
    return line
