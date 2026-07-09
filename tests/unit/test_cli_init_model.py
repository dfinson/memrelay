"""Unit tests for ``init``'s embedding-model prefetch (E10-S1 / #13 remaining gap).

``init`` must *trigger the one-time model download* while staying idempotent and never
failing setup if the download can't happen. These tests stay fully offline: the real
fastembed download is never triggered. The download trigger — ``build_embedder`` — is
faked via ``sys.modules`` so no network, no onnxruntime, and no heavy graphiti import.

The autouse ``stub_model_prefetch`` fixture (see ``conftest.py``) neutralizes the seam for
every other unit test; here we restore the *real* seam (via ``real_prefetch``) so ``init``
exercises the genuine prefetch logic end-to-end, with only the download itself faked.
"""

from __future__ import annotations

import sys
import types

from click.testing import CliRunner

from memrelay import cli
from memrelay.cli import main
from memrelay.config import Config


def _fake_graphiti(monkeypatch, build_embedder) -> None:
    """Install a fake ``memrelay.engine.graphiti`` exposing ``build_embedder``.

    Keeps these tests light (no graphiti_core / ladybug import) and lets us drive the
    download trigger's success/failure deterministically.
    """
    module = types.ModuleType("memrelay.engine.graphiti")
    module.build_embedder = build_embedder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "memrelay.engine.graphiti", module)


def test_init_invokes_model_prefetch_once(cli_env, stub_model_prefetch) -> None:
    """``init`` calls the prefetch seam exactly once with the resolved config."""
    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert len(stub_model_prefetch) == 1
    assert isinstance(stub_model_prefetch[0], Config)


def test_init_downloads_model_when_absent(cli_env, real_prefetch, monkeypatch) -> None:
    """A first run constructs the embedder (the download trigger) and reports done."""
    monkeypatch.setattr(cli, "_prefetch_embedding_model", real_prefetch)
    calls: list = []

    def fake_build_embedder(cfg):
        calls.append(cfg)
        models = cfg.home_path / "models"
        models.mkdir(parents=True, exist_ok=True)
        (models / "model.onnx").write_bytes(b"x")
        return object()

    _fake_graphiti(monkeypatch, fake_build_embedder)

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1, "build_embedder is the download trigger and must be called once"
    lowered = result.output.lower()
    assert "downloading" in lowered
    assert "done" in lowered


def test_init_skips_download_when_model_present(cli_env, real_prefetch, monkeypatch) -> None:
    """Re-run with the model cached: no re-download, stays fast, clear message."""
    home, _ = cli_env
    models = home / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "model.onnx").write_bytes(b"x")

    def must_not_download(cfg):
        raise AssertionError("build_embedder must not run when the model is already cached")

    _fake_graphiti(monkeypatch, must_not_download)
    monkeypatch.setattr(cli, "_prefetch_embedding_model", real_prefetch)

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert "already present" in result.output.lower()


def test_init_survives_model_download_failure(cli_env, real_prefetch, monkeypatch) -> None:
    """A download failure is non-fatal: config + MCP still land and init exits 0."""
    home, copilot = cli_env
    monkeypatch.setattr(cli, "_prefetch_embedding_model", real_prefetch)

    def boom(cfg):
        raise RuntimeError("offline")

    _fake_graphiti(monkeypatch, boom)

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert (home / "config.toml").is_file(), "config must still be written"
    assert (copilot / "mcp-config.json").is_file(), "MCP must still be registered"
    assert "could not download" in result.output.lower()


def test_init_prefetch_is_noop_for_non_local_provider(cli_env, real_prefetch, monkeypatch) -> None:
    """A byo-key embeddings provider has no local model to fetch."""
    monkeypatch.setattr(cli, "_prefetch_embedding_model", real_prefetch)
    monkeypatch.setenv("MEMRELAY_EMBEDDINGS__PROVIDER", "openai")

    def must_not_download(cfg):
        raise AssertionError("no local download for a non-local provider")

    _fake_graphiti(monkeypatch, must_not_download)

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert "none to prefetch" in result.output.lower()
