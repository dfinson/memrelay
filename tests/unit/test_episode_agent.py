"""Unit tests for the ``_episode_agent`` source_description parser (E5-S4, #65).

Cross-agent recall's optional ``agent`` filter / ``prefer_agent`` boost decide which
episodes belong to an agent *purely* by parsing the ``source_description`` string that
``MemoryEngine.note`` wrote (E5-S3 / #40). This is the sibling of ``_episode_repo`` and
locks the same encoding's forms — most importantly that the repo-only, bare-repo, and
sentinel forms (and empty/absent values) yield ``None`` so an un-attributed note is never
mis-classified as belonging to an agent.
"""

from __future__ import annotations

import pytest

from memrelay.engine.graphiti import _episode_agent


@pytest.mark.parametrize(
    ("source_description", "expected"),
    [
        ("repo=owner/name agent=copilot", "copilot"),  # repo + agent provenance (#40)
        ("agent=claude", "claude"),  # agent only (no repo)
        ("repo=owner/name", None),  # repo only -> NOT an agent
        ("owner/name", None),  # bare repo form (pre-#40) -> NOT an agent
        ("memrelay-note", None),  # sentinel -> NOT an agent
        (None, None),  # absent
        ("", None),  # empty
        ("   ", None),  # whitespace-only
    ],
)
def test_episode_agent_parses_all_provenance_forms(source_description, expected):
    assert _episode_agent(source_description) == expected


def test_episode_agent_returns_agent_verbatim():
    # The agent id is returned as-written; case-folding for matching happens in search(),
    # not here, so a mixed-case provider id round-trips unchanged.
    assert _episode_agent("repo=Owner/Repo-A agent=Copilot") == "Copilot"


def test_episode_agent_is_token_order_independent():
    # note() always writes repo before agent, but the parser must not depend on that — it
    # finds the agent= token wherever it sits and ignores the repo= token.
    assert _episode_agent("agent=claude repo=owner/name") == "claude"


def test_episode_agent_ignores_empty_agent_value():
    # A dangling ``agent=`` (no value) is not a real agent tag; it must not match everything.
    assert _episode_agent("repo=owner/name agent=") is None
    assert _episode_agent("agent=") is None


@pytest.mark.parametrize(
    ("source_description", "expected"),
    [
        ("agent=github%20copilot", "github copilot"),  # embedded space in agent
        ("repo=r agent=claude%20code", "claude code"),  # SPEC §5.3 first-class 'claude code'
        ("agent=a%3Db", "a=b"),  # embedded '=' in agent
        ("agent=re%2520po", "re%20po"),  # literal '%20' survives (not decoded to a space)
        ("repo=my%20org/name agent=copilot", "copilot"),  # a space in repo can't break agent
    ],
)
def test_episode_agent_roundtrips_escaped_values(source_description, expected):
    assert _episode_agent(source_description) == expected


def test_episode_agent_rejects_forged_agent_token_in_repo_value():
    # The real agent survives; the ``agent=admin`` the caller tried to smuggle through the
    # repo value is escaped into the ``repo=`` token and never parsed as the agent.
    forged = "repo=owner/name%20agent%3Dadmin agent=copilot"
    assert _episode_agent(forged) == "copilot"


def test_episode_agent_ignores_agent_like_substring_in_bare_repo():
    # A bare repo (source falsy) is not a tokenized description, so a literal ``agent=``
    # sitting inside it must never be mistaken for an agent tag.
    assert _episode_agent("weird agent=notme") is None
