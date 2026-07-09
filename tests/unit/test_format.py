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
        "scores": [0.9, 0.8],
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
    rendered = format_as_map(
        {
            "nodes": [{"uuid": "a", "name": "A"}, {"uuid": "b", "name": "B"}],
            "edges": [],
            "scores": [0.5],  # only one score for two nodes
        }
    )

    _assert_valid_mermaid(rendered)
    assert "_(score 0.50)_" in rendered  # first node scored
    assert "**B**" in rendered  # second node still rendered, no score


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
