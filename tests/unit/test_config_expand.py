"""Unit tests for config path/var expansion and graph ``connection`` coercion.

``load_config`` expands ``~`` and ``$VAR`` / ``${VAR}`` / ``%VAR%`` references in paths against
an **injected** ``environ`` mapping (that injection is what makes the config defaults tests
hermetic). ``test_config.py`` exercises the end-to-end defaults; this file pins the three helpers
directly against explicit env mappings — every home-var fallback, every var syntax, the
unknown-var/tilde-user "leave it alone" branches, and ``_graph_from_dict``'s connection coercion —
so the isolation contract can't silently regress into reading the real process environment.
"""

from __future__ import annotations

import pytest

from memrelay.config import (
    ConfigError,
    GraphConnectionConfig,
    _expand,
    _expanduser_with,
    _expandvars_with,
    _graph_from_dict,
)

# ─── _expanduser_with ────────────────────────────────────────────────────────


def test_expanduser_prefers_home_then_userprofile() -> None:
    assert _expanduser_with("~/g.db", {"HOME": "/home/me"}) == "/home/me/g.db"
    # No HOME -> fall back to USERPROFILE (the Windows variable).
    assert _expanduser_with("~/g.db", {"USERPROFILE": r"C:\Users\me"}) == r"C:\Users\me/g.db"


def test_expanduser_falls_back_to_homedrive_plus_homepath() -> None:
    # Only the split HOMEDRIVE/HOMEPATH pair is present -> they are concatenated.
    env = {"HOMEDRIVE": "C:", "HOMEPATH": r"\Users\me"}
    assert _expanduser_with("~/g.db", env) == r"C:\Users\me/g.db"


def test_expanduser_leaves_tilde_user_form_untouched() -> None:
    # ``~alice`` is the unsupported per-user form: returned verbatim even when HOME is set.
    assert _expanduser_with("~alice/g.db", {"HOME": "/home/me"}) == "~alice/g.db"


def test_expanduser_keeps_literal_tilde_when_no_home_vars() -> None:
    # Nothing to expand against -> the literal ``~`` path is preserved (not blanked).
    assert _expanduser_with("~/g.db", {}) == "~/g.db"


def test_expanduser_ignores_paths_without_leading_tilde() -> None:
    assert _expanduser_with("/abs/g.db", {"HOME": "/home/me"}) == "/abs/g.db"


# ─── _expandvars_with ────────────────────────────────────────────────────────


@pytest.mark.parametrize("template", ["$FOO/x", "${FOO}/x", "%FOO%/x"])
def test_expandvars_expands_every_supported_syntax(template: str) -> None:
    assert _expandvars_with(template, {"FOO": "/base"}) == "/base/x"


def test_expandvars_leaves_unknown_variables_intact() -> None:
    # An undefined reference is emitted verbatim (matched text), not blanked to "".
    assert _expandvars_with("$BAR/x", {}) == "$BAR/x"
    assert _expandvars_with("${BAR}/x", {}) == "${BAR}/x"
    assert _expandvars_with("%BAR%/x", {}) == "%BAR%/x"


def test_expandvars_expands_multiple_references_in_one_string() -> None:
    env = {"A": "1", "B": "2", "C": "3"}
    assert _expandvars_with("$A-${B}-%C%", env) == "1-2-3"


# ─── _graph_from_dict connection coercion ────────────────────────────────────


def test_graph_from_dict_passes_through_prebuilt_connection() -> None:
    # A kwarg override may already be a GraphConnectionConfig; it must pass through as-is.
    conn = GraphConnectionConfig(uri="bolt://db")
    result = _graph_from_dict({"backend": "neo4j", "connection": conn})

    assert result.backend == "neo4j"
    assert result.connection is conn  # same object -> not re-coerced


def test_graph_from_dict_coerces_dict_connection_and_drops_unknown_keys() -> None:
    result = _graph_from_dict(
        {"backend": "neo4j", "connection": {"uri": "bolt://db", "bogus": "dropme"}}
    )

    assert result.connection == GraphConnectionConfig(uri="bolt://db")
    assert not hasattr(result.connection, "bogus")


def test_graph_from_dict_yields_none_connection_for_embedded_default() -> None:
    # ladybug default carries no connection; absent -> None (not an empty config object).
    assert _graph_from_dict({"backend": "ladybug"}).connection is None
    # A non-dict, non-config value is also normalized to None rather than crashing.
    assert _graph_from_dict({"backend": "ladybug", "connection": "nonsense"}).connection is None


# ─── _expand fail-loud on an unresolved variable (#153, F2) ──────────────────


def test_expand_rejects_unset_variable_via_production_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset ``${VAR}`` must be rejected on the *production* expandvars path.

    ``_expand`` with ``environ=None`` uses ``os.path.expandvars`` against the real
    process environment — the path the daemon actually takes (the ``home_path`` /
    ``graph_path`` properties call it that way). An unset reference is left as a literal
    token which, unguarded, ``Path.resolve()`` would silently turn into a
    current-directory-relative path, misplacing the graph DB with no diagnostic. It must
    instead fail loud, naming the offending variable.
    """
    monkeypatch.delenv("RT153_UNSET_DIR", raising=False)

    with pytest.raises(ConfigError) as excinfo:
        _expand("${RT153_UNSET_DIR}/graph.db")

    assert "RT153_UNSET_DIR" in str(excinfo.value)
