"""Unit tests for LocalEmbedder helpers (E4-S3 / #36).

The real 384-dim fastembed vector is asserted in the integration gate (where the
model download is shared and cached); these unit tests stay offline.
"""

from __future__ import annotations

from memrelay.engine.embedder import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    _as_text_list,
)


def test_as_text_list_wraps_single_string():
    assert _as_text_list("hello") == ["hello"]


def test_as_text_list_passes_through_list():
    assert _as_text_list(["a", "b"]) == ["a", "b"]


def test_as_text_list_stringifies_non_strings():
    assert _as_text_list([1, 2]) == ["1", "2"]


def test_embedding_defaults():
    assert DEFAULT_EMBEDDING_DIM == 384
    assert "bge-small" in DEFAULT_EMBEDDING_MODEL
