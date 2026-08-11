"""Unit tests for ``init``'s embedding-model prefetch (E10-S1 / #13 remaining gap).

``init`` must bootstrap the frozen model through its lock authority while staying
idempotent and never failing setup if the download can't happen. These tests stay fully
offline: the lock materializer is faked, so no network or FastEmbed runtime is loaded.

The autouse ``stub_model_prefetch`` fixture (see ``conftest.py``) neutralizes the seam for
every other unit test; here we restore the *real* seam (via ``real_prefetch``) so ``init``
exercises the genuine prefetch logic end-to-end, with only the download itself faked.
"""

from __future__ import annotations

from click.testing import CliRunner

from memrelay import cli
from memrelay.cli import main
from memrelay.config import Config


def test_init_invokes_model_prefetch_once(cli_env, stub_model_prefetch) -> None:
    """``init`` calls the prefetch seam exactly once with the resolved config."""
    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert len(stub_model_prefetch) == 1
    assert isinstance(stub_model_prefetch[0], Config)


def test_init_downloads_model_when_absent(cli_env, real_prefetch, monkeypatch) -> None:
    """A first run invokes the lock-owned materializer and reports done."""
    monkeypatch.setattr(cli, "_prefetch_embedding_model", real_prefetch)
    calls: list = []

    def fake_materialize(cache_dir):
        calls.append(cache_dir)
        models = cache_dir
        models.mkdir(parents=True, exist_ok=True)
        return object()

    monkeypatch.setattr(
        "memrelay.engine.model_lock.materialize_verified_embedding_model", fake_materialize
    )

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1, "the lock materializer must be called once"
    lowered = result.output.lower()
    assert "preparing" in lowered
    assert "done" in lowered


def test_init_rechecks_model_when_files_are_present(cli_env, real_prefetch, monkeypatch) -> None:
    """A stale-looking cache cannot skip authority verification on an init re-run."""
    home, _ = cli_env
    models = home / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "model.onnx").write_bytes(b"x")
    calls: list = []

    def fake_materialize(cache_dir):
        calls.append(cache_dir)
        return object()

    monkeypatch.setattr(
        "memrelay.engine.model_lock.materialize_verified_embedding_model", fake_materialize
    )
    monkeypatch.setattr(cli, "_prefetch_embedding_model", real_prefetch)

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1


def test_init_survives_model_download_failure(cli_env, real_prefetch, monkeypatch) -> None:
    """A download failure is non-fatal: config + MCP still land and init exits 0."""
    home, copilot = cli_env
    monkeypatch.setattr(cli, "_prefetch_embedding_model", real_prefetch)

    def boom(_cache_dir):
        raise RuntimeError("offline")

    monkeypatch.setattr("memrelay.engine.model_lock.materialize_verified_embedding_model", boom)

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert (home / "config.toml").is_file(), "config must still be written"
    assert (copilot / "mcp-config.json").is_file(), "MCP must still be registered"
    assert "could not download" in result.output.lower()


def test_init_prefetch_is_noop_for_non_local_provider(cli_env, real_prefetch, monkeypatch) -> None:
    """A byo-key embeddings provider has no local model to fetch."""
    monkeypatch.setattr(cli, "_prefetch_embedding_model", real_prefetch)
    monkeypatch.setenv("MEMRELAY_EMBEDDINGS__PROVIDER", "openai")

    def must_not_materialize(_cache_dir):
        raise AssertionError("no local model materialization for a non-local provider")

    monkeypatch.setattr(
        "memrelay.engine.model_lock.materialize_verified_embedding_model",
        must_not_materialize,
    )

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert "none to prefetch" in result.output.lower()
