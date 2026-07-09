"""Unit tests for the optional ``[namespaces.*]`` config surface (E5-S2, #41).

memrelay lets a user declare, in ``config.toml``, which repos share a memory
namespace via ``[namespaces.<name>]`` sections with ``repos = ["owner/name", ...]``.
:func:`memrelay.config.load_config` parses those into an immutable
:class:`~memrelay.config.NamespacesConfig` and exposes a derived repo→namespace
``repo_map`` on ``Config`` (read later by the #39 resolver — never here).

These tests are hermetic and offline: they drive ``load_config`` with an injected
``environ`` and either explicit ``namespaces=`` kwargs or a temporary TOML file, so
they never touch a real home directory, network, or model download.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from memrelay.config import (
    EmbeddingsConfig,
    GraphConfig,
    IngestConfig,
    LLMConfig,
    Namespace,
    NamespaceConfigError,
    NamespacesConfig,
    load_config,
)

# ─── Absence / zero-config guard ─────────────────────────────────────────────


def test_absent_section_yields_empty_map_and_unchanged_defaults() -> None:
    """No ``[namespaces.*]`` → empty map and byte-identical existing defaults."""
    cfg = load_config(environ={})
    assert cfg.namespaces == NamespacesConfig()
    assert cfg.namespaces.entries == ()
    assert cfg.namespaces.repo_map == {}
    # The optional section must not perturb any pre-existing default.
    assert cfg.graph == GraphConfig()
    assert cfg.llm == LLMConfig()
    assert cfg.embeddings == EmbeddingsConfig()
    assert cfg.ingest == IngestConfig()


def test_namespace_config_error_is_a_value_error() -> None:
    """Subclassing ValueError keeps broad value-error guards working."""
    assert issubclass(NamespaceConfigError, ValueError)


# ─── Valid parsing ───────────────────────────────────────────────────────────


def test_valid_multi_namespace_from_toml_file(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
        [namespaces.work]
        repos = ["owner/repoA", "owner/repoB"]

        [namespaces.personal]
        repos = ["me/proj"]
        """,
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file, environ={})

    # Declaration order is preserved on ``entries``.
    assert [ns.name for ns in cfg.namespaces.entries] == ["work", "personal"]
    assert cfg.namespaces.repo_map == {
        "owner/repoa": "work",
        "owner/repob": "work",
        "me/proj": "personal",
    }


def test_valid_multi_namespace_via_kwargs() -> None:
    cfg = load_config(
        environ={},
        namespaces={
            "work": {"repos": ["owner/a", "owner/b"]},
            "side": {"repos": ["me/c"]},
        },
    )
    assert cfg.namespaces.entries == (
        Namespace(name="work", repos=("owner/a", "owner/b")),
        Namespace(name="side", repos=("me/c",)),
    )
    assert cfg.namespaces.namespace_for("owner/a") == "work"
    assert cfg.namespaces.namespace_for("me/c") == "side"


# ─── Normalization ───────────────────────────────────────────────────────────


def test_repo_keys_are_lowercased_and_trimmed() -> None:
    cfg = load_config(
        environ={},
        namespaces={"work": {"repos": ["Owner/RepoA", "  owner/RepoB  "]}},
    )
    assert cfg.namespaces.repo_map == {"owner/repoa": "work", "owner/repob": "work"}
    # ``namespace_for`` normalizes its argument the same way, so a differently-cased
    # or whitespace-padded query still resolves.
    assert cfg.namespaces.namespace_for("OWNER/REPOA") == "work"
    assert cfg.namespaces.namespace_for(" owner/repob ") == "work"
    assert cfg.namespaces.namespace_for("unknown/repo") is None


def test_namespace_name_trimmed_but_case_preserved() -> None:
    """Names are the memory scope (graphiti group_id): trim only, no case-fold."""
    cfg = load_config(
        environ={},
        namespaces={"  Work  ": {"repos": ["a/b"]}},
    )
    assert cfg.namespaces.entries[0].name == "Work"
    assert cfg.namespaces.repo_map == {"a/b": "Work"}


def test_intra_namespace_duplicate_repo_is_deduped() -> None:
    """The same repo twice in one namespace collapses to one entry (no error)."""
    cfg = load_config(
        environ={},
        namespaces={"work": {"repos": ["a/b", "A/B", "c/d"]}},
    )
    assert cfg.namespaces.entries[0].repos == ("a/b", "c/d")
    assert cfg.namespaces.repo_map == {"a/b": "work", "c/d": "work"}


# ─── Validation errors ───────────────────────────────────────────────────────


def test_duplicate_repo_across_namespaces_errors() -> None:
    """A repo in two namespaces (detected case-insensitively) fails loudly."""
    with pytest.raises(NamespaceConfigError) as exc:
        load_config(
            environ={},
            namespaces={
                "work": {"repos": ["Owner/Repo"]},
                "personal": {"repos": ["owner/repo"]},
            },
        )
    msg = str(exc.value)
    assert "owner/repo" in msg
    assert "work" in msg and "personal" in msg


def test_repos_not_a_list_errors() -> None:
    with pytest.raises(NamespaceConfigError, match=r"work.*repos.*list"):
        load_config(environ={}, namespaces={"work": {"repos": "owner/repo"}})


def test_repo_entry_not_a_string_errors() -> None:
    with pytest.raises(NamespaceConfigError, match=r"work.*invalid repo 123"):
        load_config(environ={}, namespaces={"work": {"repos": ["ok/repo", 123]}})


@pytest.mark.parametrize("bad", ["no-slash", "a/b/c", "/name", "owner/", "   ", ""])
def test_malformed_repo_shape_errors(bad: str) -> None:
    with pytest.raises(NamespaceConfigError, match=r"invalid repo"):
        load_config(environ={}, namespaces={"work": {"repos": [bad]}})


def test_empty_namespace_name_errors() -> None:
    with pytest.raises(NamespaceConfigError, match=r"name must be a non-empty"):
        load_config(environ={}, namespaces={"   ": {"repos": ["a/b"]}})


def test_missing_repos_key_errors() -> None:
    with pytest.raises(NamespaceConfigError, match=r"work.*missing.*repos"):
        load_config(environ={}, namespaces={"work": {}})


def test_namespace_body_not_a_table_errors() -> None:
    with pytest.raises(NamespaceConfigError, match=r"work.*must be a table"):
        load_config(environ={}, namespaces={"work": ["a/b"]})


@pytest.mark.parametrize("bad", ["oops", ["a", "b"], 5])
def test_namespaces_not_a_table_errors(bad: object) -> None:
    with pytest.raises(NamespaceConfigError, match=r"\[namespaces\] must be a table"):
        load_config(environ={}, namespaces=bad)


# ─── Immutability & serialization ────────────────────────────────────────────


def test_config_types_are_frozen() -> None:
    cfg = load_config(environ={}, namespaces={"work": {"repos": ["a/b"]}})
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.namespaces.entries[0].name = "nope"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.namespaces.entries = ()  # type: ignore[misc]


def test_repo_map_is_a_fresh_copy() -> None:
    """Mutating a returned ``repo_map`` must not corrupt the config's state."""
    cfg = load_config(environ={}, namespaces={"work": {"repos": ["a/b"]}})
    grabbed = cfg.namespaces.repo_map
    grabbed["x/y"] = "hacked"
    assert "x/y" not in cfg.namespaces.repo_map


def test_to_dict_with_namespaces_is_json_serializable() -> None:
    """``Config.to_dict()`` feeds ``memrelay config`` → json.dumps; keep it safe."""
    cfg = load_config(
        environ={},
        namespaces={"work": {"repos": ["owner/a", "owner/b"]}},
    )
    data = json.loads(json.dumps(cfg.to_dict()))
    entries = data["namespaces"]["entries"]
    assert entries == [{"name": "work", "repos": ["owner/a", "owner/b"]}]


def test_defaults_to_dict_has_empty_namespaces() -> None:
    cfg = load_config(environ={})
    data = json.loads(json.dumps(cfg.to_dict()))
    assert data["namespaces"] == {"entries": []}
