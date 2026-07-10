"""Unit tests for the ``agent`` filter / ``prefer_agent`` boost ranking helpers (E5-S4, #65).

These are the pure, graph-free core of cross-agent recall's optional ``prefer_agent`` boost:
given a map of result uuid -> the (lower-cased) agents that produced its source episodes, the
soft boost floats one agent's items up as a stable tiebreaker. The agent tag is a soft signal
only — never a hard filter (SPEC §5.3) — so there is no agent-exclusive filter to test. Testing
the helpers directly (with hand-built provenance maps) proves both boost directions
deterministically, with no dependency on graph extraction — the end-to-end wiring is proved
separately by ``tests/integration/test_cross_agent_unify.py``.
"""

from __future__ import annotations

from memrelay.engine.graphiti import (
    _agent_match,
    _agent_rank,
    _boost_agent_edges,
    _boost_agent_pairs,
)

# Four items spanning every provenance case: copilot-only, claude-only, both, and unmapped.
PAIRS = [
    ({"uuid": "n-cop", "name": "Zephyr"}, 0.9),
    ({"uuid": "n-claude", "name": "Quasar"}, 0.8),
    ({"uuid": "n-both", "name": "Widget"}, 0.7),
    ({"uuid": "n-none", "name": "Orphan"}, 0.6),
]
NODE_PROV = {
    "n-cop": {"copilot"},
    "n-claude": {"claude"},
    "n-both": {"copilot", "claude"},
    # "n-none" deliberately absent -> no parseable agent provenance.
}

EDGES = [
    {"uuid": "e-cop", "name": "e1"},
    {"uuid": "e-claude", "name": "e2"},
    {"uuid": "e-both", "name": "e3"},
    {"uuid": "e-none", "name": "e4"},
]
EDGE_PROV = {
    "e-cop": {"copilot"},
    "e-claude": {"claude"},
    "e-both": {"copilot", "claude"},
}


def _uuids(pairs):
    return [node["uuid"] for node, _ in pairs]


def test_agent_match_and_rank():
    assert _agent_match("n-cop", NODE_PROV, "copilot") is True
    assert _agent_match("n-cop", NODE_PROV, "claude") is False
    assert _agent_match("n-both", NODE_PROV, "claude") is True
    assert _agent_match("n-none", NODE_PROV, "copilot") is False  # unmapped never matches
    assert _agent_rank("n-cop", NODE_PROV, "copilot") == 0
    assert _agent_rank("n-claude", NODE_PROV, "copilot") == 1


def test_boost_pairs_floats_preferred_agent_stably_both_directions():
    # prefer copilot: copilot-tagged (n-cop, n-both) to the front in original order; the rest
    # (n-claude, n-none) keep their original relative order behind them.
    boosted_cop = _boost_agent_pairs(PAIRS, NODE_PROV, "copilot")
    assert _uuids(boosted_cop) == ["n-cop", "n-both", "n-claude", "n-none"]
    # prefer claude: claude-tagged (n-claude, n-both) float up instead.
    boosted_claude = _boost_agent_pairs(PAIRS, NODE_PROV, "claude")
    assert _uuids(boosted_claude) == ["n-claude", "n-both", "n-cop", "n-none"]


def test_boost_keeps_scores_aligned_with_nodes():
    # The (node, score) pairs are sorted jointly, so every node keeps its own score.
    expected = {"n-cop": 0.9, "n-claude": 0.8, "n-both": 0.7, "n-none": 0.6}
    for node, score in _boost_agent_pairs(PAIRS, NODE_PROV, "copilot"):
        assert score == expected[node["uuid"]]


def test_boost_edges_floats_preferred_agent_stably():
    boosted = [e["uuid"] for e in _boost_agent_edges(EDGES, EDGE_PROV, "claude")]
    assert boosted == ["e-claude", "e-both", "e-cop", "e-none"]


def test_boost_with_unmatched_agent_is_a_stable_no_op():
    # No item carries "ghost", so every item ranks 1 and the stable sort preserves order —
    # the same identity guarantee that makes the default (no prefer_agent) path byte-identical.
    assert _uuids(_boost_agent_pairs(PAIRS, NODE_PROV, "ghost")) == _uuids(PAIRS)
    assert [e["uuid"] for e in _boost_agent_edges(EDGES, EDGE_PROV, "ghost")] == [
        e["uuid"] for e in EDGES
    ]
