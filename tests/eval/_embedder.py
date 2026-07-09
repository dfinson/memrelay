"""Deterministic, offline embedder for the retrieval eval (E11-S4 / #21).

A fixed hashed bag-of-words projection into a 384-dimensional unit vector. It is
byte-stable run-to-run and machine-to-machine — no model download, no network, no
fastembed/ONNX version drift — so precision@k measured over ``engine.search`` is
reproducible and can back a CI regression gate.

Injected via ``MemoryEngine.from_config(embedder=...)``, which bypasses
``build_embedder`` (and therefore fastembed) entirely, exactly as the hermetic
integration fixtures do. This is deliberately the same family as the integration
suite's offline fallback embedder; the eval keeps its own copy so it never imports
test fixtures and is not coupled to their evolution.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from graphiti_core.embedder.client import EmbedderClient

EMBED_DIM = 384


class DeterministicEmbedder(EmbedderClient):
    """Hashed bag-of-words -> L2-normalized 384-d vector. Deterministic and offline.

    Cosine similarity between two texts reduces to their normalized lexical
    overlap, which — fused with graphiti's BM25 full-text channel under RRF — is
    enough to rank distinctive-vocabulary facts, while staying perfectly
    reproducible for a regression baseline.
    """

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * EMBED_DIM
        for token in text.lower().split():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
            vec[int.from_bytes(digest, "big") % EMBED_DIM] += 1.0
        norm = math.sqrt(sum(value * value for value in vec)) or 1.0
        return [value / norm for value in vec]

    async def create(self, input_data: Any) -> list[float]:
        texts = [input_data] if isinstance(input_data, str) else list(input_data)
        first = texts[0] if texts else ""
        return self._vector(first if isinstance(first, str) else str(first))

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in input_data_list]
