"""Unit tests for the ``prefer_repo`` boost ranking helpers (E8/SPEC §4.4).

These are the pure, graph-free core of recall's optional ``prefer_repo`` boost, and the
direct sibling of the ``prefer_agent`` helpers in ``test_agent_provenance.py``. ``repo`` is
not a first-class node property in graphiti's model, so the signal is an intentionally
**soft substring match** over an item's ``name``/``fact``/``summary`` — never a hard filter.
Testing the helpers directly (with hand-built rows) proves the ranking deterministically,
with no dependency on the graph; the end-to-end wiring is proved separately by
``tests/integration`` cross-agent/recall tests.
"""

from __future__ import annotations

from memrelay.engine.graphiti import (
    _boost_repo_edges,
    _boost_repo_pairs,
    _repo_rank,
)

REPO = "dfinson/memrelay"

# Nodes carry the repo signal in different fields (name, summary), plus rows that carry
# none — so a boost must float the two matches up while leaving the rest where they are.
PAIRS = [
    ({"uuid": "n-name", "name": "dfinson/memrelay auth"}, 0.9),  # match in name
    ({"uuid": "n-plain", "name": "Widget", "summary": "unrelated"}, 0.8),  # no match
    ({"uuid": "n-sum", "name": "Thing", "summary": "lives in dfinson/memrelay"}, 0.7),  # in summary
    ({"uuid": "n-none", "name": "Orphan"}, 0.6),  # no match
]

# Edges match on name/fact (edge rows carry no summary).
EDGES = [
    {"uuid": "e-name", "name": "dfinson/memrelay", "fact": "f1"},  # match in name
    {"uuid": "e-plain", "name": "REL", "fact": "nothing"},  # no match
    {"uuid": "e-fact", "name": "REL", "fact": "touches dfinson/memrelay"},  # match in fact
    {"uuid": "e-none", "name": "REL"},  # no match (no fact key)
]


def _uuids(pairs):
    return [node["uuid"] for node, _ in pairs]


# --- _repo_rank: the soft substring signal ------------------------------------


def test_repo_rank_matches_any_of_name_fact_summary():
    # A hit in *any* of the three scanned fields ranks 0 (floats up).
    assert _repo_rank({"name": "x dfinson/memrelay y"}, "memrelay") == 0
    assert _repo_rank({"fact": "touches dfinson/memrelay"}, "memrelay") == 0
    assert _repo_rank({"summary": "the dfinson/memrelay repo"}, "memrelay") == 0
    # No mention anywhere -> rank 1.
    assert _repo_rank({"name": "Widget", "summary": "unrelated"}, "memrelay") == 1


def test_repo_rank_is_case_insensitive():
    # The haystack is lower-cased, so a mixed-case field still matches a lower-case needle.
    assert _repo_rank({"name": "DFINSON/MemRelay"}, "memrelay") == 0


def test_repo_rank_is_none_and_missing_field_safe():
    # The ``or ""`` guards mean None-valued or absent fields never raise.
    assert _repo_rank({}, "memrelay") == 1
    assert _repo_rank({"name": None, "fact": None, "summary": None}, "memrelay") == 1


# --- _boost_repo_pairs: stable float-up, scores stay aligned -------------------


def test_boost_pairs_floats_repo_matches_up_stably():
    boosted = _boost_repo_pairs(PAIRS, REPO)
    # Matches (n-name, n-sum) float to the front in their original relative order; the
    # non-matches (n-plain, n-none) keep their original relative order behind them.
    assert _uuids(boosted) == ["n-name", "n-sum", "n-plain", "n-none"]


def test_boost_pairs_matches_substring_case_insensitively():
    # prefer_repo is lower-cased before matching, and a bare repo *name* still hits the
    # full ``owner/name`` mention (soft substring signal).
    expected = ["n-name", "n-sum", "n-plain", "n-none"]
    assert _uuids(_boost_repo_pairs(PAIRS, "Dfinson/MemRelay")) == expected
    assert _uuids(_boost_repo_pairs(PAIRS, "MEMRELAY")) == expected


def test_boost_keeps_each_score_with_its_node():
    # Pairs are sorted jointly, so a re-ordered node keeps its own score.
    expected = {"n-name": 0.9, "n-plain": 0.8, "n-sum": 0.7, "n-none": 0.6}
    for node, score in _boost_repo_pairs(PAIRS, REPO):
        assert score == expected[node["uuid"]]


def test_boost_pairs_unmatched_repo_is_a_stable_no_op():
    # No row mentions "ghost", so every row ranks 1 and the stable sort preserves order —
    # the identity guarantee that keeps the default (no prefer_repo) path byte-identical.
    assert _uuids(_boost_repo_pairs(PAIRS, "ghost")) == _uuids(PAIRS)


# --- _boost_repo_edges: same soft signal over the (score-less) edge list -------


def test_boost_edges_floats_repo_matches_up_stably():
    boosted = [edge["uuid"] for edge in _boost_repo_edges(EDGES, REPO)]
    assert boosted == ["e-name", "e-fact", "e-plain", "e-none"]


def test_boost_edges_unmatched_repo_is_a_stable_no_op():
    assert [edge["uuid"] for edge in _boost_repo_edges(EDGES, "ghost")] == [
        edge["uuid"] for edge in EDGES
    ]
