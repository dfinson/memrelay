"""Local, key-less text embeddings via fastembed (E4-S3 / #36).

Implements graphiti-core's ``EmbedderClient`` using a small quantized ONNX
model that runs entirely on-device, so the default memory engine needs no API
keys and makes no network calls after the one-time model download.

Model: ``BAAI/bge-small-en-v1.5`` (384-dimensional). fastembed downloads a
quantized ONNX build on first use and caches it under ``cache_dir`` (the engine
points this at ``~/.memrelay/models`` per SPEC §6.3).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path

from graphiti_core.embedder.client import EmbedderClient

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_EMBEDDING_DIM = 384


def _as_text_list(input_data: str | Iterable[str]) -> list[str]:
    """Coerce graphiti's embedder input into a list of strings.

    The ``EmbedderClient`` contract also allows pre-tokenized int sequences, but
    fastembed works on raw text and graphiti only ever hands us strings for the
    Kuzu path; anything non-string is stringified defensively.
    """
    if isinstance(input_data, str):
        return [input_data]
    texts: list[str] = []
    for item in input_data:
        texts.append(item if isinstance(item, str) else str(item))
    return texts


class LocalEmbedder(EmbedderClient):
    """On-device fastembed implementation of graphiti's ``EmbedderClient``."""

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        cache_dir: str | Path | None = None,
    ) -> None:
        # Imported lazily so merely importing the engine package does not pull in
        # fastembed/onnxruntime (keeps CLI import time and test collection light).
        from fastembed import TextEmbedding

        self.model_name = model_name
        self.cache_dir = str(cache_dir) if cache_dir is not None else None
        self._model = TextEmbedding(model_name=model_name, cache_dir=self.cache_dir)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(texts)]

    async def create(self, input_data: str | Iterable[str]) -> list[float]:
        """Embed ``input_data`` and return a single 384-dim vector.

        Per the ``EmbedderClient`` contract this returns one vector; when handed
        multiple inputs graphiti expects the first. The CPU-bound embed runs in a
        worker thread so it never blocks the daemon's event loop.
        """
        texts = _as_text_list(input_data)
        vectors = await asyncio.to_thread(self._embed_sync, texts)
        return vectors[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        """Embed a batch of strings, returning one vector per input."""
        texts = _as_text_list(input_data_list)
        return await asyncio.to_thread(self._embed_sync, texts)
