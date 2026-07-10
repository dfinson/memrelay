"""Unit tests for the deterministic namespace resolver (issue #39, E5-S1).

``resolve_namespace`` is a pure function implementing the SPEC §5.2 precedence:

1. an explicit ``[namespaces.*]`` config map (#41), matched **case-insensitively**
   because #41 stores its keys lowercased+stripped while ``current_repo`` returns the
   git remote's ``owner/name`` verbatim (often mixed-case),
2. the GitHub owner derived from ``owner/name``,
3. the OS username, when there is no remote / not a repo (``repo is None``).

Every test patches ``getpass.getuser`` so the username fallback is deterministic and
no test accidentally depends on the machine's real login name.
"""

from __future__ import annotations

import pytest

from memrelay.config import _namespaces_from_dict
from memrelay.mcp import namespace

OS_USER = "local-user"


@pytest.fixture(autouse=True)
def _fixed_os_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``getpass.getuser`` so the username fallback is hermetic."""
    monkeypatch.setattr(namespace.getpass, "getuser", lambda: OS_USER)


# --- precedence #1: explicit config map ---------------------------------------


def test_config_map_takes_precedence_over_owner() -> None:
    """A config-mapped repo resolves to its namespace, not the GitHub owner."""
    assert namespace.resolve_namespace("dfinson/memrelay", {"dfinson/memrelay": "acme"}) == "acme"


def test_config_map_matches_case_insensitively() -> None:
    """The #41 contract: a mixed-case remote still hits a lowercased config key."""
    # current_repo() returns the remote URL verbatim; #41 keys are lowercased.
    assert namespace.resolve_namespace("Dfinson/MemRelay", {"dfinson/memrelay": "acme"}) == "acme"


def test_config_map_normalizes_surrounding_whitespace() -> None:
    """Normalization mirrors config._normalize_repo (strip + lower), not just lower."""
    assert (
        namespace.resolve_namespace("  Dfinson/MemRelay  ", {"dfinson/memrelay": "acme"}) == "acme"
    )


# --- precedence #2: GitHub owner ----------------------------------------------


@pytest.mark.parametrize("empty_map", [None, {}])
def test_github_owner_when_no_config_map(empty_map: dict[str, str] | None) -> None:
    """With no usable map, the namespace is the owner half of ``owner/name``."""
    assert namespace.resolve_namespace("dfinson/memrelay", empty_map) == "dfinson"


def test_github_owner_when_repo_absent_from_map() -> None:
    """A repo not declared in the map falls through to its owner."""
    assert namespace.resolve_namespace("other/thing", {"dfinson/memrelay": "acme"}) == "other"


# --- precedence #3: OS username (no remote) -----------------------------------


def test_os_username_when_no_remote() -> None:
    """No repo (no remote / not a git repo) -> the OS username."""
    assert namespace.resolve_namespace(None) == OS_USER


def test_map_ignored_when_repo_is_none() -> None:
    """A map can't apply without a repo id -> still the OS username."""
    assert namespace.resolve_namespace(None, {"dfinson/memrelay": "acme"}) == OS_USER


# --- fork handling (judgment call, #39) ---------------------------------------


def test_fork_resolves_to_fork_owner() -> None:
    """A fork's ``origin`` is the fork owner, so by default it gets its own namespace.

    ``current_repo`` reads only ``git remote get-url origin``; for a fork of
    ``dfinson/memrelay`` that is ``alice/memrelay``. This keeps resolution
    deterministic (single remote) rather than guessing an ``upstream`` remote.
    """
    assert namespace.resolve_namespace("alice/memrelay", {}) == "alice"


def test_fork_can_be_aliased_into_shared_namespace_via_config() -> None:
    """The config map is the explicit escape hatch to share a fork's memory pool."""
    assert namespace.resolve_namespace("alice/memrelay", {"alice/memrelay": "teammem"}) == "teammem"


# --- resolve_context: threads the map + returns repo provenance verbatim -------


def test_resolve_context_threads_map_and_returns_repo_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(namespace, repo): namespace comes from the normalized map hit; repo is verbatim."""
    monkeypatch.setattr(namespace, "current_repo", lambda cwd=None: "Dfinson/MemRelay")

    ns, repo = namespace.resolve_context("/some/cwd", {"dfinson/memrelay": "acme"})

    assert ns == "acme"
    assert repo == "Dfinson/MemRelay"


def test_resolve_context_no_remote_falls_back_to_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No remote -> (OS username, None)."""
    monkeypatch.setattr(namespace, "current_repo", lambda cwd=None: None)

    ns, repo = namespace.resolve_context("/some/cwd")

    assert ns == OS_USER
    assert repo is None


# --- normalization symmetry through the REAL config parser (issue #39) ---------


def test_config_map_normalization_is_symmetric_through_the_real_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard the 3-way normalization contract end-to-end, not just a hand-built dict.

    ``NamespacesConfig.repo_map`` surfaces ``entry.repos`` **verbatim**; key
    normalization happens earlier in the section parser (``_namespaces_from_dict`` ->
    ``_validate_repo_entry`` -> ``config._normalize_repo``). The resolver normalizes the
    *lookup* key via ``namespace._normalize_repo``. Correctness therefore depends on
    ``parser-normalizes-keys`` <=> ``resolver-normalizes-lookup``. The other map tests
    hand-build a pre-lowercased dict, so they'd all still pass if one side later drifted
    (e.g. the parser starts stripping a ``.git`` suffix and the resolver forgets to). This
    routes a MIXED-CASE + WHITESPACE raw declaration through the real parser so the two
    sides can't silently diverge.
    """
    repo_map = _namespaces_from_dict({"acme": {"repos": ["  Dfinson/MemRelay  "]}}).repo_map

    # A differently-cased remote still resolves to the parser-produced namespace.
    assert namespace.resolve_namespace("Dfinson/MemRelay", repo_map) == "acme"

    # Same guarantee through the full observe-path resolution (repo returned verbatim).
    monkeypatch.setattr(namespace, "current_repo", lambda cwd=None: "Dfinson/MemRelay")
    assert namespace.resolve_context("/some/cwd", repo_map) == ("acme", "Dfinson/MemRelay")
