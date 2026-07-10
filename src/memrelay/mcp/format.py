"""Render daemon results into agent-facing text (SPEC §4.3/§4.4, ``mcp/format.py``).

Recall (:func:`format_as_map`) renders the recalled subgraph as a Mermaid ``graph
LR`` diagram plus a scannable key-facts list with drill-down hints (SPEC §4.3). The
diagram is *always* syntactically valid Mermaid regardless of the free-text node and
edge labels: every label is emitted double-quoted through :func:`_mermaid_label`
(which strips newlines/control chars and neutralizes quote/angle characters), nodes
use stable synthetic ids (``n0``, ``n1``, … in input order), and every node is
declared before any edge references it. The shapes consumed here match the daemon
wire schema, so they do not change when the real engine lands.

Recall additionally reduces the rendered subgraph to the *relevant* nodes (E8-S2,
``mcp/format.py``): using the node-aligned reranker ``scores`` it keeps nodes at or above
the median score, tightens to a natural score gap when one dominates, and rescues
well-connected nodes (``>= _RELEVANCE_MIN_DEGREE`` incident edges) so structural hubs
survive. This is a display-only reduction — the raw recall the retrieval eval measures is
untouched — and it never turns a non-empty result into the not-found text.

Recall then renders each kept node at a *density tier* proportional to its score (E8-S3,
``mcp/format.py``): a node whose score is at or above the median of the kept scores is
"high" and rendered in full — its ``summary`` (facts) plus its edges — while a lower-scored
node stays "compact", showing only its name and a drill-down hint (its ``uuid`` handle is
always kept, so every entity stays resolvable). An edge is rendered when at least one
endpoint is high, so a high node keeps all of its links (and a degree-rescued hub keeps its
link to the core) while a link solely between two compact nodes is left for drill-down. The
tiering is display-only and pure: with no numeric scores every node is high (identical to
pre-E8-S3), and the top-scored node is always high, so a non-empty result never collapses to
the not-found text.
"""

from __future__ import annotations

import math
import re
import statistics
from typing import Any

_NO_RESULTS = "No relevant memories found."

#: Longest a Mermaid label may get before it is truncated (keeps the diagram compact).
_MERMAID_MAX_LABEL = 80
#: Any run of whitespace (including newlines/tabs) collapses to a single space.
_MERMAID_WHITESPACE = re.compile(r"\s+")
#: Control characters that must never reach a label (a raw newline breaks Mermaid).
_MERMAID_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: A node needs at least this many distinct incident edges to survive on connectivity
#: alone (E8-S2 AC3): a well-connected node is kept even when its score is below the
#: relevance cutoff, so structural hubs stay in the subgraph.
_RELEVANCE_MIN_DEGREE = 2
#: A "natural" score gap must be at least this many times the mean of the other gaps to
#: count as a cliff worth cutting at (E8-S2 AC2) — keeps a uniform score run uncut.
_GAP_DOMINANCE_RATIO = 2.0
#: The fewest scored nodes for which a gap cut is even considered; below this there is no
#: meaningful "typical gap" to measure a dominant cliff against.
_GAP_MIN_NODES = 3


def format_as_map(results: dict[str, Any]) -> str:
    """Format a ``search`` response ``{"nodes", "edges", "scores"}`` as a map.

    Emits ``## Memory Map`` -> a fenced ``mermaid`` ``graph LR`` of the subgraph -> a
    key-facts list (``### Entities`` / ``### Relationships``, each entity carrying its
    ``uuid``) -> a drill-down hint. The Mermaid block is always valid (see
    :func:`_mermaid_map`); an empty result keeps the plain not-found text.

    Before rendering, the returned nodes are reduced to the relevant subgraph via
    :func:`_select_indices` (score median + natural-gap cut, with a degree-based rescue —
    E8-S2). Kept nodes render in input order, so the synthetic ``n0``, ``n1`` ids and any
    ``prefer_*`` ordering are preserved; an edge is shown when at least one endpoint
    survives (so a boundary fact is not lost), while :func:`_mermaid_map` still draws only
    edges between two kept nodes. The reduction never turns a non-empty result into the
    not-found text.

    Each kept node then renders at a density tier proportional to its score (E8-S3): a
    high-tier node (score at/above the median of the kept scores — see
    :func:`_high_tier_flags`) renders full facts (its ``summary``) plus its edges, while a
    low-tier node stays compact (name + ``uuid`` + drill-down hint). An edge renders only
    when at least one endpoint is high-tier, so ``### Relationships`` and the Mermaid diagram
    stay in sync and a link solely between two compact nodes is left for drill-down.
    """
    nodes = results.get("nodes") or []
    edges = results.get("edges") or []
    if not nodes:
        return _NO_RESULTS

    scores = results.get("scores") or []
    kept = _select_indices(nodes, edges, scores)
    nodes = [nodes[index] for index in kept]
    scores = [scores[index] if index < len(scores) else None for index in kept]
    kept_uuids = {node["uuid"] for node in nodes if node.get("uuid")}
    edges = [
        edge
        for edge in edges
        if edge.get("source_node_uuid") in kept_uuids or edge.get("target_node_uuid") in kept_uuids
    ]

    # E8-S3 density tiers: high-tier nodes render full facts + edges; low-tier nodes stay
    # compact. An edge is visible when at least one endpoint is high-tier, so its fact is
    # owned by a full-detail node (and a degree-rescued hub keeps its link to the core).
    high_flags = _high_tier_flags(scores)
    high_uuids = {
        node["uuid"]
        for node, high in zip(nodes, high_flags, strict=True)
        if high and node.get("uuid")
    }
    visible_edges = [
        edge
        for edge in edges
        if edge.get("source_node_uuid") in high_uuids or edge.get("target_node_uuid") in high_uuids
    ]

    lines = ["## Memory Map", "", *_mermaid_map(nodes, visible_edges), "", "### Entities"]
    for index, node in enumerate(nodes):
        score = scores[index] if index < len(scores) else None
        lines.append(_format_node_line(node, score, high_flags[index]))

    if visible_edges:
        lines += ["", "### Relationships"]
        lines += [_format_edge_line(edge) for edge in visible_edges]

    lines += ["", '*Drill into any entity with `memory_detail("<uuid>")`.*']
    return "\n".join(lines)


def _is_score(value: Any) -> bool:
    """True for a usable numeric score (excludes ``None``, ``bool``, ``NaN``/``inf``)."""
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _aligned_scores(nodes: list[Any], scores: list[Any]) -> list[float | None]:
    """Return one score per node (input order); missing/non-numeric entries become ``None``."""
    return [
        scores[index] if index < len(scores) and _is_score(scores[index]) else None
        for index in range(len(nodes))
    ]


def _node_degrees(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[int, int]:
    """Count distinct incident edges per node index (a self-loop counts once).

    Measured over the *full* returned edge list — before the display-time edge reduction —
    so connectivity reflects the true recalled neighbourhood (E8-S2 AC3). An edge endpoint
    whose uuid is not among the returned nodes contributes no degree.
    """
    index_by_uuid: dict[str, int] = {}
    for index, node in enumerate(nodes):
        uuid = node.get("uuid")
        if uuid and uuid not in index_by_uuid:
            index_by_uuid[uuid] = index

    degrees: dict[int, int] = {}
    for edge in edges:
        endpoints = {edge.get("source_node_uuid"), edge.get("target_node_uuid")}
        for uuid in endpoints:
            index = index_by_uuid.get(uuid) if uuid else None
            if index is not None:
                degrees[index] = degrees.get(index, 0) + 1
    return degrees


def _relevance_cutoff(finite_scores: list[float]) -> float:
    """Score at/above which a node counts as relevant (E8-S2 AC1 median + AC2 gap).

    Starts at the median (kept inclusively downstream) and, when a single gap dominates the
    sorted scores, tightens up to that cliff. The gap can only raise the bar, never lower it
    below the median, so the cut is scale-free and each criterion stays independently
    testable.
    """
    cutoff = statistics.median(finite_scores)
    ordered = sorted(finite_scores, reverse=True)
    if len(ordered) >= _GAP_MIN_NODES:
        gaps = [ordered[i] - ordered[i + 1] for i in range(len(ordered) - 1)]
        widest = max(gaps)
        position = gaps.index(widest)
        others = gaps[:position] + gaps[position + 1 :]
        baseline = statistics.fmean(others) if others else 0.0
        if widest > 0 and (baseline == 0.0 or widest >= _GAP_DOMINANCE_RATIO * baseline):
            cutoff = max(cutoff, ordered[position])
    return cutoff


def _select_indices(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], scores: list[Any]
) -> list[int]:
    """Choose which node indices stay in the rendered subgraph (E8-S2).

    Keeps a node when it is score-relevant (``>=`` the median/gap cutoff — AC1/AC2) OR
    structurally central (``>= _RELEVANCE_MIN_DEGREE`` incident edges — AC3 rescue); a node
    that is both weakly connected and below the cutoff is dropped (AC3). With no numeric
    score the relevance signal is absent, so every node is kept. The selection is never
    empty for a non-empty input — the top-scored node is the floor.
    """
    aligned = _aligned_scores(nodes, scores)
    finite = [value for value in aligned if value is not None]
    if not finite:
        return list(range(len(nodes)))

    degrees = _node_degrees(nodes, edges)
    cutoff = _relevance_cutoff(finite)
    kept = [
        index
        for index in range(len(nodes))
        if (aligned[index] is not None and aligned[index] >= cutoff)
        or degrees.get(index, 0) >= _RELEVANCE_MIN_DEGREE
    ]
    if not kept:
        best = max(
            (index for index in range(len(nodes)) if aligned[index] is not None),
            key=lambda index: aligned[index],
        )
        kept = [best]
    return kept


def format_detail(result: dict[str, Any]) -> str:
    """Format a ``detail`` response ``{"node", "connected_edges", "episodes"}``.

    Renders the node header (raw name + uuid), a small ``mermaid`` neighborhood
    ``graph LR`` centered on the node, then its connections and episodes. An unknown
    node keeps the plain not-found text.
    """
    node = result.get("node") or {}
    if not node:
        return "Entity not found."

    name = node.get("name", "(unnamed)")
    uuid = node.get("uuid", "?")
    lines = [f"# {name}", f"`{uuid}`"]
    if node.get("summary"):
        lines += ["", node["summary"]]

    edges = result.get("connected_edges") or []
    lines += ["", *_mermaid_detail(node, edges)]

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


def _high_tier_flags(scores: list[Any]) -> list[bool]:
    """Per kept node, whether it renders at full density (E8-S3 density tiers).

    A node is high-tier when its score is finite and at/above the *median of the kept finite
    scores* (inclusive, mirroring #54's median convention), so the split is scale-free — it
    cuts an RRF-scale run (~0.01-0.05) and a stub-scale run (1.0/0.5) the same way, with no
    absolute threshold. When no kept node has a finite score there is no density signal, so
    every node is high-tier — rendering exactly as before E8-S3. Pure function of the scores
    (a median plus a per-index ``>=``): same input yields the same tiers, and the top-scored
    node is always high (the full-detail floor, so a real result never renders all-compact).
    """
    finite = [value for value in scores if _is_score(value)]
    if not finite:
        return [True] * len(scores)
    boundary = statistics.median(finite)
    return [_is_score(value) and value >= boundary for value in scores]


def _format_node_line(node: dict[str, Any], score: float | None, high: bool) -> str:
    """Render one ``### Entities`` line at its density tier (E8-S3).

    A high-tier node renders full: name, ``uuid``, score, and its ``summary`` (the facts). A
    low-tier node stays compact — name, ``uuid`` (kept so drill-down still resolves), and a
    hint — with the summary and score suffix withheld for :func:`format_detail` to surface.
    """
    uuid = node.get("uuid", "?")
    name = node.get("name", "(unnamed)")
    if not high:
        return f"- **{name}** `{uuid}` — _drill down for details_"
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


def _mermaid_map(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    """Return the fenced ``mermaid`` ``graph LR`` block for a recall subgraph.

    Nodes get stable synthetic ids (``n0``, ``n1``, … in input order) and are all
    declared before any edge, so node-only graphs render and every id an edge refers
    to is guaranteed to exist. Edges whose endpoints are not among the declared nodes
    (unknown/missing uuid) are omitted from the diagram — they still appear in the
    ``### Relationships`` text — which keeps the output valid Mermaid.
    """
    ids: dict[str, str] = {}
    block = ["```mermaid", "graph LR"]
    for index, node in enumerate(nodes):
        node_id = f"n{index}"
        uuid = node.get("uuid")
        if uuid:
            ids[uuid] = node_id
        label = _mermaid_label(node.get("name") or node.get("summary"))
        block.append(f'  {node_id}["{label}"]')
    for edge in edges:
        source = ids.get(edge.get("source_node_uuid"))
        target = ids.get(edge.get("target_node_uuid"))
        if source is None or target is None:
            continue
        block.append(_mermaid_edge(source, target, edge.get("name")))
    block.append("```")
    return block


def _mermaid_detail(node: dict[str, Any], edges: list[dict[str, Any]]) -> list[str]:
    """Return a ``mermaid`` ``graph LR`` block for one node's neighborhood.

    The node is the center (``n0``); each connected edge draws to its neighbor,
    labeled by the neighbor's uuid (itself the handle to pass back to
    ``memory_detail``). Neighbors are de-duplicated and every id is declared before it
    is referenced, so the block is always valid — even for self-loops or an edge with
    a missing endpoint uuid.
    """
    ids: dict[str, str] = {}
    declarations: list[str] = []

    def ensure(uuid: str | None, label: Any) -> str:
        key = uuid or f"__anon{len(declarations)}"
        if key not in ids:
            ids[key] = f"n{len(declarations)}"
            declarations.append(f'  {ids[key]}["{_mermaid_label(label)}"]')
        return ids[key]

    center_uuid = node.get("uuid")
    center_id = ensure(center_uuid, node.get("name") or center_uuid)

    def resolve(uuid: str | None) -> str:
        return center_id if uuid == center_uuid else ensure(uuid, uuid)

    connections = [
        _mermaid_edge(
            resolve(edge.get("source_node_uuid")),
            resolve(edge.get("target_node_uuid")),
            edge.get("name"),
        )
        for edge in edges
    ]
    return ["```mermaid", "graph LR", *declarations, *connections, "```"]


def _mermaid_edge(source: str, target: str, name: Any) -> str:
    """Render one ``graph LR`` edge, with a quoted label when the edge is named."""
    if name:
        return f'  {source} -->|"{_mermaid_label(name)}"| {target}'
    return f"  {source} --> {target}"


def _mermaid_label(value: Any) -> str:
    """Sanitize free text into an always-safe double-quoted Mermaid label body.

    Replaces control characters (including newlines/tabs) and collapses whitespace to
    single spaces so a label is always one line; neutralizes characters that can
    terminate or reinterpret a quoted label (``"``, backtick, ``|``, ``<`` / ``>``);
    truncates for compactness; never returns empty. The caller wraps the result in
    double quotes.
    """
    text = _MERMAID_CONTROL.sub(" ", "" if value is None else str(value))
    text = _MERMAID_WHITESPACE.sub(" ", text).strip()
    text = text.replace('"', "'").replace("`", "'").replace("|", "/")
    text = text.replace("<", "(").replace(">", ")")
    if len(text) > _MERMAID_MAX_LABEL:
        text = text[: _MERMAID_MAX_LABEL - 3].rstrip() + "..."
    return text or "?"
