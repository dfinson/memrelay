"""Integration-test fixtures for the memrelay memory engine (E4 gate).

Everything here is hermetic: a deterministic in-process ``MockLLMClient`` stands
in for entity/edge extraction, and embeddings come from the real ``LocalEmbedder``
when fastembed can download its model, otherwise a deterministic offline
fallback. No network, no API keys, temp Kuzu only.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
from typing import Any

import pytest
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient, ModelSize
from graphiti_core.llm_client.config import LLMConfig as GraphitiLLMConfig
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

EMBED_DIM = 384


def _canned(model_name: str | None, found: list[str]) -> dict[str, Any]:
    """Deterministic, schema-conformant responses keyed by graphiti prompt model.

    ``found`` are the entity names detected in the episode text, so the mock
    behaves like a (perfect) extractor without hard-coding a single scenario.
    """
    if model_name in ("ExtractedEntities",):
        return {
            "extracted_entities": [
                {"name": name, "entity_type_id": 0, "episode_indices": [0]} for name in found
            ]
        }
    if model_name in ("CombinedExtraction",):
        return {
            "extracted_entities": [{"name": name, "entity_type_id": 0} for name in found],
            "edges": _edges(found),
        }
    if model_name in ("ExtractedEdges",):
        return {"edges": _edges(found)}
    if model_name in ("NodeResolutions",):
        return {
            "entity_resolutions": [
                {"id": index, "name": name, "duplicate_candidate_id": -1}
                for index, name in enumerate(found)
            ]
        }
    if model_name in ("EdgeDuplicate",):
        return {"duplicate_facts": [], "contradicted_facts": []}
    if model_name in ("EdgeTimestamps", "EdgeDates"):
        return {"valid_at": None, "invalid_at": None}
    if model_name in ("BatchEdgeTimestamps",):
        return {"timestamps": []}
    if model_name in ("Summary", "SagaSummary", "EntitySummary"):
        return {"summary": "canned summary"}
    if model_name in ("SummaryDescription",):
        return {"description": "canned description"}
    if model_name in ("SummarizedEntities",):
        return {"summaries": []}
    return {}


def _edges(found: list[str]) -> list[dict[str, Any]]:
    if len(found) < 2:
        return []
    source, target = found[0], found[1]
    return [
        {
            "source_entity_name": source,
            "target_entity_name": target,
            "relation_type": "RELATES_TO",
            "fact": f"{source} uses {target}",
            "valid_at": None,
            "invalid_at": None,
            "episode_indices": [0],
        }
    ]


class MockLLMClient(LLMClient):
    """Deterministic, in-process ``LLMClient`` for hermetic extraction.

    Detects which of ``vocab`` appears in the episode text and returns
    schema-conformant JSON for every graphiti prompt model. No network.
    """

    def __init__(self, vocab: list[str]) -> None:
        super().__init__(GraphitiLLMConfig(), cache=False)
        self.vocab = list(vocab)

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 16384,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        name = response_model.__name__ if response_model else None
        text = "\n".join(str(message.content) for message in messages).lower()
        found = [entity for entity in self.vocab if entity.lower() in text]
        return _canned(name, found)


class HashingEmbedder(EmbedderClient):
    """Deterministic offline fallback embedder (hashed bag-of-words, 384-dim).

    Produces lexical-overlap cosine similarity so semantic-ish recall still works
    when the real fastembed model genuinely cannot be downloaded in CI.
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
        return self._vector(texts[0] if isinstance(texts[0], str) else str(texts[0]))

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in input_data_list]


@pytest.fixture(scope="session")
def gate_embedder() -> EmbedderClient:
    """Real fastembed ``LocalEmbedder`` if the model downloads, else offline fallback."""
    cache_dir = os.environ.get("MEMRELAY_TEST_FASTEMBED_CACHE")
    try:
        from memrelay.engine.embedder import LocalEmbedder

        embedder = LocalEmbedder(cache_dir=cache_dir)
        # Force the one-time model download now so a network failure falls back
        # cleanly instead of exploding mid-test.
        vector = asyncio.run(embedder.create("warmup"))
        assert len(vector) == EMBED_DIM
        return embedder
    except Exception:  # noqa: BLE001 - any download/runtime failure → deterministic fallback
        return HashingEmbedder()


@pytest.fixture
def mock_llm_factory():
    """Return a builder for the deterministic mock LLM (avoids test-package imports)."""

    def _make(vocab: list[str]) -> MockLLMClient:
        return MockLLMClient(vocab)

    return _make
