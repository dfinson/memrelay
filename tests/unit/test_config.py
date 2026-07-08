"""Unit tests for memrelay.config — defaults, path resolution, env + file overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from memrelay.config import (
    Config,
    candidate_config_paths,
    default_home,
    env_overrides,
    load_config,
    resolve_config_path,
)


def test_defaults_with_no_file_or_env() -> None:
    cfg = load_config(environ={})
    assert cfg.graph.backend == "kuzu"
    assert cfg.graph.path == "~/.memrelay/graph.db"
    assert cfg.llm.strategy == "borrow-host"
    assert cfg.llm.host == "copilot"
    assert cfg.embeddings.provider == "local"
    assert cfg.embeddings.model == "BAAI/bge-small-en-v1.5"


def test_home_resolution_prefers_memrelay_home(tmp_path: Path) -> None:
    target = tmp_path / "data"
    home = default_home({"MEMRELAY_HOME": str(target)})
    assert home == target.resolve()


def test_home_resolution_falls_back_to_xdg(tmp_path: Path) -> None:
    home = default_home({"XDG_DATA_HOME": str(tmp_path)})
    assert home == (tmp_path / "memrelay").resolve()


def test_explicit_config_env_short_circuits_search(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.toml"
    paths = candidate_config_paths({"MEMRELAY_CONFIG": str(explicit)})
    assert paths == [explicit.resolve()]


def test_resolve_config_path_returns_none_when_absent(tmp_path: Path) -> None:
    # Point every candidate at a non-existent dir via XDG + HOME-free env.
    env = {"XDG_CONFIG_HOME": str(tmp_path / "nope")}
    assert resolve_config_path(env) is None


def test_env_overrides_nest_and_coerce() -> None:
    env = {
        "MEMRELAY_LLM__STRATEGY": "byo-key",
        "MEMRELAY_GRAPH__BACKEND": "kuzu",
        "MEMRELAY_EMBEDDINGS__PROVIDER": "openai",
        "MEMRELAY_CONFIG": "/should/be/ignored",  # meta, not a field
        "UNRELATED": "x",
    }
    overrides = env_overrides(env)
    assert overrides == {
        "llm": {"strategy": "byo-key"},
        "graph": {"backend": "kuzu"},
        "embeddings": {"provider": "openai"},
    }


def test_env_overrides_apply_to_config() -> None:
    cfg = load_config(environ={"MEMRELAY_LLM__STRATEGY": "local", "MEMRELAY_LLM__HOST": "claude"})
    assert cfg.llm.strategy == "local"
    assert cfg.llm.host == "claude"


def test_file_load_and_precedence(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
        [llm]
        strategy = "byo-key"
        host = "codex"

        [graph]
        backend = "kuzu"
        path = "/tmp/from-file/graph.db"
        """,
        encoding="utf-8",
    )
    # File sets host=codex; env overrides host=claude; explicit kwarg wins over both.
    env = {"MEMRELAY_LLM__HOST": "claude"}
    cfg = load_config(path=cfg_file, environ=env)
    assert cfg.llm.strategy == "byo-key"  # from file
    assert cfg.llm.host == "claude"  # env beats file
    assert cfg.graph.path == "/tmp/from-file/graph.db"

    cfg2 = load_config(path=cfg_file, environ=env, llm={"host": "explicit"})
    assert cfg2.llm.host == "explicit"  # kwargs beat env + file


def test_missing_explicit_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(path=tmp_path / "does-not-exist.toml", environ={})


def test_config_path_properties_expand(tmp_path: Path) -> None:
    cfg = Config(home=str(tmp_path), graph=load_config(environ={}).graph)
    # graph.path uses ~ expansion via graph_path property
    assert cfg.graph_path.is_absolute()
    assert cfg.home_path == tmp_path.resolve()


def test_ensure_home_creates_dir(tmp_path: Path) -> None:
    from memrelay.config import ensure_home

    target = tmp_path / "mr-home"
    cfg = load_config(environ={"MEMRELAY_HOME": str(target)})
    created = ensure_home(cfg)
    assert created.is_dir()
    assert created == target.resolve()
