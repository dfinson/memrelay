"""Offline-failure behavior for ``LocalEmbedder`` (rt-engine MED: embedder offline fallback).

When fastembed cannot fetch its ONNX model (offline + uncached), it retries internally and then
raises an opaque ``ValueError("Could not load model ... from any source.")``. ``LocalEmbedder``
must translate that into a clear, actionable error naming the model, the cache dir, and the two
fixes (``memrelay init`` while online, or switch to the ``openai`` provider) — never a silent
degraded embedder, per the offline-safety invariant.

These tests simulate the offline failure with a fake ``fastembed`` module swapped into
``sys.modules`` (no network, no onnxruntime), so they are fully hermetic. A success-path test
proves the online/cached path is unchanged. All are synchronous — the failure happens in
``__init__``, so no event loop is needed.
"""

from __future__ import annotations

import sys
import types

import pytest

from memrelay.config import Config, EmbeddingsConfig
from memrelay.engine.embedder import LocalEmbedder

#: The generic error fastembed raises once every source (HF + GCS) fails while offline.
_OPAQUE = "Could not load model BAAI/bge-small-en-v1.5 from any source."


def _install_fake_fastembed(monkeypatch, *, raises=None, factory=None):
    """Swap ``sys.modules['fastembed']`` for a fake exposing ``TextEmbedding``.

    Fully hermetic: never imports the real fastembed/onnxruntime and never touches the network.
    ``raises`` makes the constructor blow up like an offline fetch; ``factory`` (a zero-arg
    callable) returns a stub model for the success path instead.
    """
    module = types.ModuleType("fastembed")

    def _text_embedding(*args, **kwargs):
        if raises is not None:
            raise raises
        return factory() if factory is not None else object()

    module.TextEmbedding = _text_embedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", module)


def test_local_embedder_offline_raises_actionable_error(monkeypatch, tmp_path):
    """An offline fetch failure surfaces the model, cache dir, and both remediations."""
    _install_fake_fastembed(monkeypatch, raises=ValueError(_OPAQUE))
    cache_dir = tmp_path / "models"

    # ``LocalEmbedderError`` subclasses ``RuntimeError``; pre-fix code raised a bare
    # ``ValueError`` here, so this ``raises(RuntimeError)`` fails against the unfixed embedder.
    with pytest.raises(RuntimeError) as excinfo:
        LocalEmbedder(model_name="BAAI/bge-small-en-v1.5", cache_dir=cache_dir)

    message = str(excinfo.value)
    assert "BAAI/bge-small-en-v1.5" in message
    assert str(cache_dir) in message
    assert "memrelay init" in message
    assert "openai" in message
    # The clear message must replace the opaque original (which carries no remediation).
    assert message != _OPAQUE


def test_local_embedder_offline_error_type_and_chains(monkeypatch, tmp_path):
    """The clear error is ``LocalEmbedderError`` and preserves the opaque original as its cause."""
    original = ValueError(_OPAQUE)
    _install_fake_fastembed(monkeypatch, raises=original)

    with pytest.raises(RuntimeError) as excinfo:
        LocalEmbedder(model_name="BAAI/bge-small-en-v1.5", cache_dir=tmp_path / "models")

    from memrelay.engine.embedder import LocalEmbedderError

    assert isinstance(excinfo.value, LocalEmbedderError)
    assert excinfo.value.__cause__ is original


def test_build_embedder_local_offline_surfaces_actionable_error(monkeypatch, tmp_path):
    """The ``build_embedder`` seam surfaces the same actionable error for ``local``."""
    _install_fake_fastembed(monkeypatch, raises=ValueError(_OPAQUE))
    from memrelay.engine.graphiti import build_embedder

    cfg = Config(home=str(tmp_path), embeddings=EmbeddingsConfig(provider="local"))

    with pytest.raises(RuntimeError) as excinfo:
        build_embedder(cfg)

    message = str(excinfo.value)
    assert "BAAI/bge-small-en-v1.5" in message
    assert "memrelay init" in message
    assert "openai" in message
    assert str(cfg.home_path / "models") in message


def test_local_embedder_success_path_unaffected(monkeypatch, tmp_path):
    """When fastembed constructs cleanly (online/cached), the embedder is built unchanged."""
    sentinel = object()
    _install_fake_fastembed(monkeypatch, factory=lambda: sentinel)

    embedder = LocalEmbedder(model_name="BAAI/bge-small-en-v1.5", cache_dir=tmp_path / "models")

    assert embedder._model is sentinel
    assert embedder.model_name == "BAAI/bge-small-en-v1.5"
    assert embedder.cache_dir == str(tmp_path / "models")
