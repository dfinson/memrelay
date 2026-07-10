"""Unit tests for the recall/detail formatters (E8-S1, SPEC §4.3).

These pin the Mermaid rendering contract: ``format_as_map`` must emit a valid
``graph LR`` for any daemon ``search`` payload — including adversarial free-text
labels — plus a key-facts list with drill-down uuids, deterministically. The
``_assert_valid_mermaid`` helper is a lightweight structural self-check reused by
every case so a break in any input surfaces as a failure, not a silently bad diagram.
"""

from __future__ import annotations

import re

from memrelay.mcp.format import format_as_map, format_detail

_NODE_RE = re.compile(r'^  (n\d+)\["(.*)"\]$')
_EDGE_RE = re.compile(r'^  (n\d+) -->(?:\|"(.*)"\|)? (n\d+)$')


def _mermaid_body(rendered: str) -> list[str]:
    """Return the lines *inside* the first ```` ```mermaid ... ``` ```` fence."""
    lines = rendered.split("\n")
    start = lines.index("```mermaid")
    end = lines.index("```", start + 1)
    return lines[start + 1 : end]


def _assert_safe_label(label: str, line: str) -> None:
    """A rendered label must carry no char that could break a quoted Mermaid label."""
    for bad in ('"', "<", ">", "|", "\n", "\r", "\t"):
        assert bad not in label, f"unsafe char {bad!r} in label: {line!r}"
    assert len(label) <= 80, f"label not truncated (len {len(label)}): {line!r}"


def _assert_valid_mermaid(rendered: str) -> list[str]:
    """Structurally validate the first Mermaid block; return its body lines.

    Enforces the always-valid contract: a ``graph LR`` header, every line is either a
    quoted node declaration or an edge, every edge references a *declared* node id, and
    no label carries a newline or an unescaped quote/angle/pipe character.
    """
    body = _mermaid_body(rendered)
    assert body, "mermaid block is empty"
    assert body[0] == "graph LR", f"missing 'graph LR' header: {body[0]!r}"

    declared: set[str] = set()
    for line in body[1:]:
        node = _NODE_RE.match(line)
        edge = _EDGE_RE.match(line)
        assert node or edge, f"unexpected mermaid line: {line!r}"
        if node:
            declared.add(node.group(1))
            _assert_safe_label(node.group(2), line)
        else:
            source, label, target = edge.group(1), edge.group(2), edge.group(3)
            assert source in declared, f"edge from undeclared node: {line!r}"
            assert target in declared, f"edge to undeclared node: {line!r}"
            if label is not None:
                _assert_safe_label(label, line)
    return body


def _sample() -> dict:
    return {
        "nodes": [
            {"uuid": "a1", "name": "AuthService", "summary": "handles login"},
            {"uuid": "j2", "name": "JWT tokens", "summary": "bearer tokens"},
        ],
        "edges": [
            {
                "name": "USES",
                "source_node_uuid": "a1",
                "target_node_uuid": "j2",
                "fact": "auth uses jwt",
            }
        ],
        "scores": [0.9, 0.9],
    }


# --------------------------------------------------------------------------- recall


def test_empty_results_return_no_memories() -> None:
    assert format_as_map({}) == "No relevant memories found."
    assert format_as_map({"nodes": [], "edges": []}) == "No relevant memories found."
    assert format_as_map({"nodes": None}) == "No relevant memories found."


def test_two_node_one_edge_renders_graph_lr() -> None:
    rendered = format_as_map(_sample())

    assert rendered.startswith("## Memory Map")
    assert _assert_valid_mermaid(rendered) == [
        "graph LR",
        '  n0["AuthService"]',
        '  n1["JWT tokens"]',
        '  n0 -->|"USES"| n1',
    ]
    # key-facts list carries the drill-down uuids + hint
    assert "### Entities" in rendered
    assert "`a1`" in rendered and "`j2`" in rendered
    assert "### Relationships" in rendered
    assert 'memory_detail("<uuid>")' in rendered


def test_nodes_without_edges_still_render() -> None:
    rendered = format_as_map({"nodes": [{"uuid": "x", "name": "Solo"}], "edges": []})

    body = _assert_valid_mermaid(rendered)
    assert body == ["graph LR", '  n0["Solo"]']
    assert not any("-->" in line for line in body)


def test_adversarial_labels_stay_valid() -> None:
    nasty = 'a "q" <b> |c| `d`\n\t[e]{f}(g);#h'
    rendered = format_as_map(
        {
            "nodes": [
                {"uuid": "n-1", "name": nasty, "summary": "s"},
                {"uuid": "n-2", "name": "x" * 300, "summary": "y"},
                {"uuid": "n-3", "name": "", "summary": ""},
            ],
            "edges": [{"name": nasty, "source_node_uuid": "n-1", "target_node_uuid": "n-2"}],
        }
    )

    # Raises on any structural break (unescaped quote, raw newline, undeclared id, ...).
    _assert_valid_mermaid(rendered)


def test_edge_to_unknown_uuid_skipped_in_graph_but_listed() -> None:
    rendered = format_as_map(
        {
            "nodes": [{"uuid": "a", "name": "A"}],
            "edges": [{"name": "REL", "source_node_uuid": "a", "target_node_uuid": "ghost"}],
        }
    )

    # No arrow to the undeclared 'ghost' node in the diagram...
    assert _assert_valid_mermaid(rendered) == ["graph LR", '  n0["A"]']
    # ...but the relationship is still visible in the key-facts text.
    assert "ghost" in rendered
    assert "-[REL]->" in rendered


def test_self_loop_renders_and_is_valid() -> None:
    rendered = format_as_map(
        {
            "nodes": [{"uuid": "a", "name": "A"}],
            "edges": [{"name": "LOOP", "source_node_uuid": "a", "target_node_uuid": "a"}],
        }
    )

    assert '  n0 -->|"LOOP"| n0' in _assert_valid_mermaid(rendered)


def test_output_is_deterministic() -> None:
    sample = _sample()
    assert format_as_map(sample) == format_as_map(sample)

    # ids follow input order: first node -> n0, second -> n1.
    body = _assert_valid_mermaid(format_as_map(sample))
    assert body[1].startswith('  n0["') and body[2].startswith('  n1["')


def test_node_without_uuid_still_declared() -> None:
    rendered = format_as_map(
        {
            "nodes": [{"name": "Nameless"}, {"uuid": "b", "name": "B"}],
            "edges": [{"name": "R", "source_node_uuid": "b", "target_node_uuid": "b"}],
        }
    )

    body = _assert_valid_mermaid(rendered)
    assert '  n0["Nameless"]' in body  # declared despite lacking a uuid
    assert '  n1["B"]' in body


def test_scores_shorter_than_nodes_do_not_crash() -> None:
    # A short scores list must not raise (missing entries pad to None); the unscored,
    # unconnected tail node carries no relevance signal, so the filter simply drops it.
    rendered = format_as_map(
        {
            "nodes": [{"uuid": "a", "name": "A"}, {"uuid": "b", "name": "B"}],
            "edges": [],
            "scores": [0.5],  # only one score for two nodes
        }
    )

    _assert_valid_mermaid(rendered)
    assert "_(score 0.50)_" in rendered  # first node scored and kept
    assert "**B**" not in rendered  # unscored dead-end filtered out (no relevance signal)


def test_label_falls_back_summary_then_placeholder() -> None:
    rendered = format_as_map(
        {
            "nodes": [
                {"uuid": "a", "summary": "only a summary"},
                {"uuid": "b"},  # neither name nor summary
            ],
            "edges": [],
        }
    )

    body = _assert_valid_mermaid(rendered)
    assert '  n0["only a summary"]' in body
    assert '  n1["?"]' in body


# ------------------------------------------------------- E8-S2 score thresholds (AC1-AC4)


def _scored(scores: list[float], edges: list[dict] | None = None) -> dict:
    """A recall payload of ``len(scores)`` uniformly-named nodes with the given scores.

    Node ``i`` is ``{"uuid": "u<i>", "name": "N<i>"}`` and ``scores[i]`` is its aligned
    reranker score, so a fixture reads as a bare score distribution.
    """
    nodes = [{"uuid": f"u{i}", "name": f"N{i}"} for i in range(len(scores))]
    return {"nodes": nodes, "edges": edges or [], "scores": list(scores)}


def _kept_uuids(rendered: str) -> list[str]:
    """The entity uuids listed under ``### Entities`` (the surviving nodes), in order."""
    section = rendered.split("### Entities", 1)[-1].split("### Relationships", 1)[0]
    return re.findall(r"`(u\d+)`", section)


def test_above_median_kept_across_score_scales() -> None:
    # (AC1) keep nodes at/above the median. RRF fusion values are tiny (~0.01-0.05)...
    rrf = format_as_map(_scored([0.05, 0.045, 0.04, 0.01, 0.008]))
    assert _kept_uuids(rrf) == ["u0", "u1", "u2"]
    _assert_valid_mermaid(rrf)
    # ...and the stub daemon's very different numeric scale cuts the same way (scale-free).
    stub = format_as_map(_scored([1.0, 0.5]))
    assert _kept_uuids(stub) == ["u0"]


def test_natural_gap_cut_tightens_beyond_median() -> None:
    # (AC2) the median alone keeps the top 3, but a gap that dominates the other drops
    # (0.045 -> 0.02) is the natural cliff, tightening the kept set to the top 2.
    rendered = format_as_map(_scored([0.05, 0.045, 0.02, 0.015, 0.01]))
    assert _kept_uuids(rendered) == ["u0", "u1"]


def test_uniform_scores_fall_back_to_median_only() -> None:
    # (AC2) no single gap dominates an even run, so nothing is cut beyond the median.
    rendered = format_as_map(_scored([5, 4, 3, 2, 1]))
    assert _kept_uuids(rendered) == ["u0", "u1", "u2"]


def test_gap_below_the_median_is_ignored() -> None:
    # (AC2) the only real cliff (7 -> 1) sits below the median, so it never re-expands or
    # over-cuts: the median result stands.
    rendered = format_as_map(_scored([10, 9, 8, 7, 1]))
    assert _kept_uuids(rendered) == ["u0", "u1", "u2"]


def test_weakly_connected_below_median_node_dropped() -> None:
    # (AC3) u2 is below the median AND a dead-end (0 edges) -> dropped as noise.
    rendered = format_as_map(_scored([0.9, 0.8, 0.1]))
    assert _kept_uuids(rendered) == ["u0", "u1"]


def test_well_connected_below_median_node_rescued() -> None:
    # (AC3) u2 scores below the median but has two incident edges (>= _RELEVANCE_MIN_DEGREE)
    # -> kept as a structural hub even though its score would drop it.
    edges = [
        {"name": "E1", "source_node_uuid": "u0", "target_node_uuid": "u2"},
        {"name": "E2", "source_node_uuid": "u1", "target_node_uuid": "u2"},
    ]
    rendered = format_as_map(_scored([0.9, 0.8, 0.1], edges))
    assert _kept_uuids(rendered) == ["u0", "u1", "u2"]
    _assert_valid_mermaid(rendered)


def test_single_edge_does_not_rescue_below_median_node() -> None:
    # (AC3) one incident edge is still 'weakly connected' (< _RELEVANCE_MIN_DEGREE), so a
    # below-median node with a single edge is dropped, not rescued.
    edges = [{"name": "E", "source_node_uuid": "u0", "target_node_uuid": "u2"}]
    rendered = format_as_map(_scored([0.9, 0.8, 0.1], edges))
    assert _kept_uuids(rendered) == ["u0", "u1"]


def test_without_scores_every_node_is_kept() -> None:
    # No numeric score anywhere means no relevance signal, so nothing is filtered.
    payload = {"nodes": [{"uuid": f"u{i}", "name": f"N{i}"} for i in range(3)], "edges": []}
    rendered = format_as_map(payload)
    assert _kept_uuids(rendered) == ["u0", "u1", "u2"]


def test_reduction_never_empties_a_real_result() -> None:
    # The always-keep floor: a non-empty recall never renders the not-found text, even for
    # a lone tiny score — the top node always survives.
    for payload in (_scored([0.001]), _scored([0.9, 0.8, 0.1, 0.05]), _scored([3, 1])):
        rendered = format_as_map(payload)
        assert rendered != "No relevant memories found."
        assert rendered.startswith("## Memory Map")
        assert _kept_uuids(rendered)  # at least one entity survives


def test_edge_to_dropped_node_survives_in_relationships() -> None:
    # (AC3/D7) u1 is filtered out, but the u0 -> u1 fact still surfaces because u0 survives
    # (>= 1 kept endpoint). The mermaid diagram omits the arrow (u1 undeclared) yet is valid.
    edges = [
        {"name": "REL", "source_node_uuid": "u0", "target_node_uuid": "u1", "fact": "kept fact"}
    ]
    rendered = format_as_map(_scored([0.9, 0.1], edges))
    assert _kept_uuids(rendered) == ["u0"]
    assert "### Relationships" in rendered
    assert "kept fact" in rendered  # boundary fact preserved
    assert _assert_valid_mermaid(rendered) == ["graph LR", '  n0["N0"]']  # no arrow to u1


def test_edge_between_two_dropped_nodes_is_removed() -> None:
    # (D7) an edge solely between two filtered-out nodes disappears entirely; a link within
    # the kept core stays.
    edges = [
        {"name": "KEEP", "source_node_uuid": "u0", "target_node_uuid": "u1", "fact": "core link"},
        {"name": "GONE", "source_node_uuid": "u2", "target_node_uuid": "u3", "fact": "orphan link"},
    ]
    rendered = format_as_map(_scored([0.9, 0.85, 0.02, 0.01], edges))
    assert _kept_uuids(rendered) == ["u0", "u1"]
    assert "core link" in rendered
    assert "orphan link" not in rendered and "GONE" not in rendered


# ------------------------------------------------------ E8-S3 density tiers (AC1-AC3)


def _entity_block(rendered: str) -> list[str]:
    """The ``### Entities`` bullet lines (one per surviving node), in input order."""
    section = rendered.split("### Entities", 1)[-1].split("### Relationships", 1)[0]
    return [line for line in section.splitlines() if line.startswith("- ")]


def _tier_split(
    scores: list[float], edges: list[dict] | None = None
) -> tuple[list[str], list[str]]:
    """Render ``_scored(...)`` and return (high-tier uuids, low-tier uuids) by entity line."""
    rendered = format_as_map(_scored(scores, edges))
    high, low = [], []
    for line in _entity_block(rendered):
        uuid = re.search(r"`(u\d+)`", line).group(1)
        (low if "drill down for details" in line else high).append(uuid)
    return high, low


def test_high_tier_renders_full_facts_and_edges() -> None:
    # (AC) a high-score node renders its summary (facts) AND its edge in ### Relationships.
    payload = {
        "nodes": [
            {"uuid": "u0", "name": "N0", "summary": "top fact"},
            {"uuid": "u1", "name": "N1", "summary": "second fact"},
        ],
        "edges": [
            {"name": "REL", "source_node_uuid": "u0", "target_node_uuid": "u1", "fact": "linked"}
        ],
        "scores": [0.9, 0.9],  # equal -> both at/above the median -> both high (full)
    }
    rendered = format_as_map(payload)

    assert _kept_uuids(rendered) == ["u0", "u1"]
    assert "top fact" in rendered and "second fact" in rendered  # summaries (facts) shown
    assert "_(score 0.90)_" in rendered  # score suffix shown
    assert "### Relationships" in rendered and "linked" in rendered  # edges shown
    assert "drill down for details" not in rendered  # nothing compact
    _assert_valid_mermaid(rendered)


def test_low_tier_is_compact_without_summary_or_score() -> None:
    # (AC) a kept-but-lower node stays compact: name + uuid + hint; summary and score withheld.
    payload = {
        "nodes": [
            {"uuid": "u0", "name": "N0", "summary": "s0"},
            {"uuid": "u1", "name": "N1", "summary": "s1"},
            {"uuid": "u2", "name": "N2", "summary": "s2"},
            {"uuid": "u3", "name": "N3", "summary": "s3"},
            {"uuid": "u4", "name": "N4", "summary": "s4"},
        ],
        "edges": [],
        "scores": [0.9, 0.8, 0.7, 0.1, 0.05],  # #54 keeps u0/u1/u2; kept-median 0.8 -> u2 low
    }
    rendered = format_as_map(payload)

    assert _kept_uuids(rendered) == ["u0", "u1", "u2"]
    low = [line for line in _entity_block(rendered) if "`u2`" in line][0]
    assert "N2" in low and "drill down for details" in low  # name + hint kept
    assert "s2" not in rendered  # summary withheld from the compact node
    assert "score" not in low  # no score suffix on the compact line
    assert "s0" in rendered and "s1" in rendered  # the high nodes keep their facts
    assert "_(score 0.90)_" in rendered
    _assert_valid_mermaid(rendered)


def test_tiering_is_scale_free_across_score_scales() -> None:
    # (AC) tiers derive from the median of the kept scores, so a stub-scale run and an
    # RRF-scale run with the same shape split into the same high/low sets (no absolute cutoff).
    stub = _tier_split([1.0, 0.9, 0.8, 0.1, 0.05])
    rrf = _tier_split([0.05, 0.045, 0.04, 0.005, 0.0025])
    assert stub == rrf == (["u0", "u1"], ["u2"])


def test_degree_rescued_hub_is_low_tier_but_keeps_core_links() -> None:
    # (D3) a node kept only by the degree>=2 rescue scores below the kept median -> low tier
    # (compact, summary withheld), yet its edges to the high core still render.
    edges = [
        {"name": "E1", "source_node_uuid": "u0", "target_node_uuid": "u2", "fact": "hub link one"},
        {"name": "E2", "source_node_uuid": "u1", "target_node_uuid": "u2", "fact": "hub link two"},
    ]
    payload = {
        "nodes": [
            {"uuid": "u0", "name": "N0", "summary": "s0"},
            {"uuid": "u1", "name": "N1", "summary": "s1"},
            {"uuid": "u2", "name": "Hub", "summary": "hub summary"},
        ],
        "edges": edges,
        "scores": [0.9, 0.8, 0.1],  # u2 below cutoff but degree 2 -> rescued, and low tier
    }
    rendered = format_as_map(payload)

    assert _kept_uuids(rendered) == ["u0", "u1", "u2"]
    hub = [line for line in _entity_block(rendered) if "`u2`" in line][0]
    assert "drill down for details" in hub  # low tier by score
    assert "hub summary" not in rendered  # its own summary withheld
    assert "hub link one" in rendered and "hub link two" in rendered  # links to the core survive
    _assert_valid_mermaid(rendered)


def test_low_to_low_edge_suppressed_but_low_to_high_kept() -> None:
    # (D2) an edge renders only when >=1 endpoint is high: the two low-high links stay while
    # the link solely between the two compact nodes is suppressed from text AND diagram.
    edges = [
        {"name": "HL", "source_node_uuid": "u2", "target_node_uuid": "u0", "fact": "low to high"},
        {"name": "LL", "source_node_uuid": "u2", "target_node_uuid": "u3", "fact": "low to low"},
        {"name": "LH", "source_node_uuid": "u3", "target_node_uuid": "u1", "fact": "other to high"},
    ]
    payload = {
        "nodes": [{"uuid": f"u{i}", "name": f"N{i}"} for i in range(4)],
        "edges": edges,
        "scores": [0.9, 0.9, 0.1, 0.1],  # u2/u3 degree-rescued -> kept, low tier
    }
    rendered = format_as_map(payload)

    assert _kept_uuids(rendered) == ["u0", "u1", "u2", "u3"]
    assert "low to high" in rendered and "other to high" in rendered
    assert "low to low" not in rendered and "LL" not in rendered  # low-low link left for drill-down
    _assert_valid_mermaid(rendered)


def test_only_low_low_edges_drops_the_relationships_section() -> None:
    # (D2) when every visible edge is suppressed, the ### Relationships header is dropped, yet
    # the result is still a valid, non-empty map (never the not-found text).
    edges = [
        {"name": "LL1", "source_node_uuid": "u2", "target_node_uuid": "u3", "fact": "buried one"},
        {"name": "LL2", "source_node_uuid": "u3", "target_node_uuid": "u2", "fact": "buried two"},
    ]
    payload = {
        "nodes": [
            {"uuid": "u0", "name": "N0", "summary": "s0"},
            {"uuid": "u1", "name": "N1", "summary": "s1"},
            {"uuid": "u2", "name": "N2"},
            {"uuid": "u3", "name": "N3"},
        ],
        "edges": edges,
        "scores": [0.9, 0.8, 0.1, 0.1],  # u2/u3 rescued (low tier); their only links are low-low
    }
    rendered = format_as_map(payload)

    assert _kept_uuids(rendered) == ["u0", "u1", "u2", "u3"]
    assert "### Relationships" not in rendered
    assert "buried one" not in rendered and "buried two" not in rendered
    assert rendered != "No relevant memories found."
    _assert_valid_mermaid(rendered)


def test_unscored_recall_renders_every_node_full() -> None:
    # (D1) no numeric score means no density signal, so every kept node renders full -
    # identical to pre-E8-S3 output (no compact lines).
    payload = {
        "nodes": [
            {"uuid": "u0", "name": "N0", "summary": "s0"},
            {"uuid": "u1", "name": "N1", "summary": "s1"},
        ],
        "edges": [{"name": "R", "source_node_uuid": "u0", "target_node_uuid": "u1", "fact": "f"}],
    }
    rendered = format_as_map(payload)

    assert _kept_uuids(rendered) == ["u0", "u1"]
    assert "s0" in rendered and "s1" in rendered  # both full
    assert "drill down for details" not in rendered  # nothing compact
    assert "### Relationships" in rendered and "f" in rendered
    _assert_valid_mermaid(rendered)


def test_top_node_always_full_and_result_never_empty() -> None:
    # (D4/floor) the top-scored node is always high tier, so a real recall never renders as
    # all-compact and never collapses to the not-found text.
    for payload in (_scored([0.001]), _scored([0.9, 0.8, 0.1, 0.05]), _scored([3, 1])):
        rendered = format_as_map(payload)
        assert rendered.startswith("## Memory Map")
        assert rendered != "No relevant memories found."
        entities = _entity_block(rendered)
        assert entities
        assert "drill down for details" not in entities[0]  # the top entity renders full


def test_every_kept_node_exposes_a_drilldown_uuid() -> None:
    # (AC) both tiers keep the back-ticked uuid handle, so every rendered entity stays
    # resolvable via memory_detail regardless of density.
    payload = {
        "nodes": [
            {"uuid": "aaaa", "name": "N0", "summary": "s0"},
            {"uuid": "bbbb", "name": "N1", "summary": "s1"},
            {"uuid": "cccc", "name": "N2", "summary": "s2"},
        ],
        "edges": [],
        "scores": [0.9, 0.85, 0.8],  # #54 keeps aaaa/bbbb; kept-median 0.875 -> aaaa high, bbbb low
    }
    rendered = format_as_map(payload)

    entities = _entity_block(rendered)
    for line in entities:
        assert re.search(r"`[0-9a-z]+`", line), f"entity line lacks a drill-down handle: {line!r}"
    assert any("drill down for details" in line for line in entities)  # a low node is present
    assert any("_(score" in line for line in entities)  # a high node is present


def test_tiered_output_is_deterministic() -> None:
    # (D4) tiering is a pure function of the scores: a mixed-tier payload renders identically.
    payload = {
        "nodes": [
            {"uuid": "u0", "name": "N0", "summary": "s0"},
            {"uuid": "u1", "name": "N1", "summary": "s1"},
            {"uuid": "u2", "name": "N2", "summary": "s2"},
        ],
        "edges": [{"name": "R", "source_node_uuid": "u0", "target_node_uuid": "u2", "fact": "f02"}],
        "scores": [0.9, 0.8, 0.7],
    }
    first = format_as_map(payload)
    assert first == format_as_map(payload)
    # sanity: this fixture actually spans both tiers.
    assert "drill down for details" in first and "_(score" in first


# --------------------------------------------------------------------------- detail


def _detail_sample() -> dict:
    return {
        "node": {"uuid": "c0", "name": "Center", "summary": "the middle"},
        "connected_edges": [{"name": "LINKS", "source_node_uuid": "c0", "target_node_uuid": "c1"}],
        "episodes": [{"name": "ep", "content": "saw it"}],
    }


def test_detail_unknown_node_returns_not_found() -> None:
    assert format_detail({}) == "Entity not found."
    assert format_detail({"node": None}) == "Entity not found."


def test_detail_renders_raw_name_and_valid_mermaid() -> None:
    detail = _detail_sample()
    rendered = format_detail(detail)

    assert detail["node"]["name"] in rendered  # raw name preserved (frozen contract)
    body = _assert_valid_mermaid(rendered)
    assert '  n0["Center"]' in body
    assert any(line.endswith(" n1") for line in body)  # edge drawn to the neighbor


def test_detail_adversarial_center_name_valid() -> None:
    rendered = format_detail(
        {
            "node": {"uuid": "c0", "name": 'weird "name" <tag>|x'},
            "connected_edges": [],
            "episodes": [],
        }
    )

    assert '# weird "name" <tag>|x' in rendered  # raw header keeps the original text
    _assert_valid_mermaid(rendered)  # mermaid block still valid


def test_detail_minimal_node_is_node_only_graph() -> None:
    rendered = format_detail({"node": {"uuid": "solo", "name": "Solo"}})

    assert _assert_valid_mermaid(rendered) == ["graph LR", '  n0["Solo"]']


def test_unnamed_edge_renders_unlabeled_arrow() -> None:
    """An edge with no ``name`` renders a bare ``-->`` (no ``|"..."|`` label), still valid."""
    rendered = format_as_map(
        {
            "nodes": [{"uuid": "a", "name": "A"}, {"uuid": "b", "name": "B"}],
            "edges": [{"source_node_uuid": "a", "target_node_uuid": "b"}],  # no "name"
        }
    )

    body = _assert_valid_mermaid(rendered)
    assert "  n0 --> n1" in body  # bare arrow...
    assert not any('-->|"' in line for line in body)  # ...never a labeled one


def test_detail_dedupes_repeated_neighbor_but_draws_every_edge() -> None:
    """Two connections to the *same* neighbor declare it once but draw both edges."""
    rendered = format_detail(
        {
            "node": {"uuid": "c0", "name": "Center"},
            "connected_edges": [
                {"name": "L1", "source_node_uuid": "c0", "target_node_uuid": "c1"},
                {"name": "L2", "source_node_uuid": "c0", "target_node_uuid": "c1"},  # same c1
            ],
        }
    )

    body = _assert_valid_mermaid(rendered)  # dedup keeps every edge id declared -> still valid
    # The shared neighbor c1 is declared exactly once (the dedup branch)...
    assert len([line for line in body if line.endswith('["c1"]')]) == 1
    # ...yet both distinct relationships are drawn.
    assert len([line for line in body if "-->" in line]) == 2
