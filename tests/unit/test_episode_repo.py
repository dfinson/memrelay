"""Unit tests for the ``_episode_repo`` source_description parser (E9-S1, #58).

``forget --repo`` decides which episodes belong to a repo *purely* by parsing the
``source_description`` string that ``MemoryEngine.note`` wrote (E5-S3 / #40). Because
the delete is IRREVERSIBLE, this locks all of that encoding's forms — most
importantly that the two provenance-less forms (agent-only, sentinel) and
empty/absent values yield ``None``, so an un-tagged or agent-only note can never be
mis-classified as belonging to a repo and silently deleted.
"""

from __future__ import annotations

import pytest

from memrelay.engine.graphiti import _episode_repo


@pytest.mark.parametrize(
    ("source_description", "expected"),
    [
        ("repo=owner/name agent=copilot", "owner/name"),  # repo + agent provenance (#40)
        ("repo=owner/name", "owner/name"),  # repo only
        ("owner/name", "owner/name"),  # bare repo form (pre-#40)
        ("agent=copilot", None),  # agent-only -> NOT a repo
        ("memrelay-note", None),  # sentinel -> NOT a repo
        (None, None),  # absent
        ("", None),  # empty
        ("   ", None),  # whitespace-only
    ],
)
def test_episode_repo_parses_all_provenance_forms(source_description, expected):
    assert _episode_repo(source_description) == expected


def test_episode_repo_returns_repo_verbatim():
    # The repo is returned as-written; case-folding for matching happens in _forget_repo,
    # not here, so a mixed-case remote round-trips unchanged.
    assert _episode_repo("repo=Owner/Repo-A agent=copilot") == "Owner/Repo-A"


def test_episode_repo_is_token_order_independent():
    # note() always writes repo first, but the parser must not depend on that — it finds
    # the repo= token wherever it sits and ignores the agent= token.
    assert _episode_repo("agent=copilot repo=owner/name") == "owner/name"


def test_episode_repo_ignores_empty_repo_value():
    # A dangling ``repo=`` (no value) is not a real repo tag; it must not match everything.
    assert _episode_repo("repo= agent=copilot") is None
