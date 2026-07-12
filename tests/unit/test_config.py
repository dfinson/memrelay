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


def test_defaults_with_no_file_or_env(tmp_path: Path) -> None:
    # Inject a controlled environment so resolution is isolated from the real home
    # and any MEMRELAY_*/XDG_* the dev or CI environment might carry (config._expand
    # honors the injected environ). tmp_path holds no config file → pure defaults.
    env = {
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    cfg = load_config(environ=env)
    assert cfg.graph.backend == "ladybug"
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
        "MEMRELAY_GRAPH__BACKEND": "neo4j",
        "MEMRELAY_EMBEDDINGS__PROVIDER": "openai",
        "MEMRELAY_CONFIG": "/should/be/ignored",  # meta, not a field
        "UNRELATED": "x",
    }
    overrides = env_overrides(env)
    assert overrides == {
        "llm": {"strategy": "byo-key"},
        "graph": {"backend": "neo4j"},
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
        backend = "neo4j"
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


def test_ingest_flags_default_off() -> None:
    """ML inferencers are off by default (SPEC §3.3, delta #7 — lean E0 pipeline)."""
    cfg = load_config(environ={})
    assert cfg.ingest.enable_phase is False
    assert cfg.ingest.enable_boundary is False


def test_ingest_flags_via_kwargs() -> None:
    """Explicit overrides flip the ingest flags (later epics opt in)."""
    cfg = load_config(environ={}, ingest={"enable_phase": True, "enable_boundary": True})
    assert cfg.ingest.enable_phase is True
    assert cfg.ingest.enable_boundary is True


def test_ingest_flags_via_env_coerce() -> None:
    """``MEMRELAY_INGEST__ENABLE_PHASE`` nests and coerces to bool like other flags."""
    cfg = load_config(
        environ={
            "MEMRELAY_INGEST__ENABLE_PHASE": "true",
            "MEMRELAY_INGEST__ENABLE_BOUNDARY": "no",
        }
    )
    assert cfg.ingest.enable_phase is True
    assert cfg.ingest.enable_boundary is False


def test_ingest_flags_via_file_and_env_precedence(tmp_path: Path) -> None:
    """An ``[ingest]`` TOML section loads; env still beats file."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
        [ingest]
        enable_phase = true
        enable_boundary = true
        """,
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file, environ={})
    assert cfg.ingest.enable_phase is True
    assert cfg.ingest.enable_boundary is True

    cfg2 = load_config(path=cfg_file, environ={"MEMRELAY_INGEST__ENABLE_PHASE": "false"})
    assert cfg2.ingest.enable_phase is False  # env beats file
    assert cfg2.ingest.enable_boundary is True  # untouched file value survives


def test_spool_budget_defaults_disabled() -> None:
    """Zero-config: the disk budget is off (byte-identical to pre-#33 append-only)."""
    cfg = load_config(environ={})
    assert cfg.ingest.spool_max_bytes == 0
    assert cfg.ingest.spool_compaction_pct == 0.9


def test_spool_budget_via_kwargs() -> None:
    """Explicit overrides set the budget and high-water fraction (E3-S4 #33)."""
    cfg = load_config(
        environ={}, ingest={"spool_max_bytes": 5_000_000, "spool_compaction_pct": 0.75}
    )
    assert cfg.ingest.spool_max_bytes == 5_000_000
    assert cfg.ingest.spool_compaction_pct == 0.75


def test_spool_budget_via_env_coerce() -> None:
    """``MEMRELAY_INGEST__SPOOL_MAX_BYTES`` coerces to int and ``…_PCT`` to float."""
    cfg = load_config(
        environ={
            "MEMRELAY_INGEST__SPOOL_MAX_BYTES": "1048576",
            "MEMRELAY_INGEST__SPOOL_COMPACTION_PCT": "0.8",
        }
    )
    assert cfg.ingest.spool_max_bytes == 1048576
    assert isinstance(cfg.ingest.spool_max_bytes, int)
    assert cfg.ingest.spool_compaction_pct == 0.8


def test_spool_budget_via_file_and_env_precedence(tmp_path: Path) -> None:
    """An ``[ingest]`` TOML budget loads; env still beats file."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
        [ingest]
        spool_max_bytes = 2000000
        spool_compaction_pct = 0.6
        """,
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file, environ={})
    assert cfg.ingest.spool_max_bytes == 2000000
    assert cfg.ingest.spool_compaction_pct == 0.6

    cfg2 = load_config(path=cfg_file, environ={"MEMRELAY_INGEST__SPOOL_MAX_BYTES": "999"})
    assert cfg2.ingest.spool_max_bytes == 999  # env beats file
    assert cfg2.ingest.spool_compaction_pct == 0.6  # untouched file value survives


def test_refactor_invalidation_lines_defaults_disabled() -> None:
    """Zero-config: file-refactor invalidation is off (E9-S3 #60) — byte-identical default."""
    cfg = load_config(environ={})
    assert cfg.ingest.refactor_invalidation_lines == 0


def test_refactor_invalidation_lines_via_kwargs() -> None:
    """An explicit threshold opts into big-refactor invalidation (E9-S3 #60)."""
    cfg = load_config(environ={}, ingest={"refactor_invalidation_lines": 150})
    assert cfg.ingest.refactor_invalidation_lines == 150


def test_refactor_invalidation_lines_via_env_coerce() -> None:
    """``MEMRELAY_INGEST__REFACTOR_INVALIDATION_LINES`` nests and coerces to int."""
    cfg = load_config(environ={"MEMRELAY_INGEST__REFACTOR_INVALIDATION_LINES": "80"})
    assert cfg.ingest.refactor_invalidation_lines == 80
    assert isinstance(cfg.ingest.refactor_invalidation_lines, int)


def test_refactor_invalidation_lines_via_file_and_env_precedence(tmp_path: Path) -> None:
    """An ``[ingest]`` TOML threshold loads; env still beats file (E9-S3 #60)."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
        [ingest]
        refactor_invalidation_lines = 120
        """,
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file, environ={})
    assert cfg.ingest.refactor_invalidation_lines == 120

    cfg2 = load_config(path=cfg_file, environ={"MEMRELAY_INGEST__REFACTOR_INVALIDATION_LINES": "5"})
    assert cfg2.ingest.refactor_invalidation_lines == 5  # env beats file
