"""Always-run hermetic e2e: agent+repo provenance lands on each episode (E5-S3 #40).

Proves the whole point of #40 end-to-end against a *real* embedded ``MemoryEngine``:
two facts noted into ONE namespace with DISTINCT ``(repo, agent)`` each must each carry
their OWN parseable ``source_description`` on the stored episode — so recall can later
attribute (and, future work, prefer) same-repo/same-agent memories.

Unlike the ``@pytest.mark.integration`` engine tests this is deliberately **unmarked**
so it runs on every ``pytest`` invocation (the provenance seam is the deliverable and
must never silently regress). It is fully hermetic: a deterministic in-process mock LLM
and an offline hashing embedder — **copied inline here on purpose** rather than imported
from ``tests/integration/conftest.py`` (no cross-test-tree imports) — plus embedded
Ladybug on ``tmp_path``. No network, no API key, never a real ``~/.memrelay``.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from typing import Any

from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient, ModelSize
from graphiti_core.llm_client.config import LLMConfig as GraphitiLLMConfig
from graphiti_core.nodes import EpisodicNode
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from memrelay.config import load_config
from memrelay.engine.graphiti import MemoryEngine

EMBED_DIM = 384
NAMESPACE = "team-ns"

# Two facts, one namespace, DISTINCT (repo, agent) each — the provenance under test.
FACTS = [
    ("acme/widgets", "copilot", "Widgets fact: the acme widgets service uses postgres."),
    ("globex/gadgets", "claude", "Gadgets fact: the globex gadgets service uses redis."),
]


class _MockLLMClient(LLMClient):
    """Deterministic, in-process ``LLMClient`` (copied from the integration doubles)."""

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
        if name in ("ExtractedEntities",):
            return {
                "extracted_entities": [
                    {"name": entity, "entity_type_id": 0, "episode_indices": [0]}
                    for entity in found
                ]
            }
        if name in ("CombinedExtraction",):
            return {
                "extracted_entities": [{"name": entity, "entity_type_id": 0} for entity in found],
                "edges": [],
            }
        if name in ("ExtractedEdges",):
            return {"edges": []}
        if name in ("NodeResolutions",):
            return {
                "entity_resolutions": [
                    {"id": index, "name": entity, "duplicate_candidate_id": -1}
                    for index, entity in enumerate(found)
                ]
            }
        if name in ("EdgeDuplicate",):
            return {"duplicate_facts": [], "contradicted_facts": []}
        if name in ("EdgeTimestamps", "EdgeDates"):
            return {"valid_at": None, "invalid_at": None}
        if name in ("BatchEdgeTimestamps",):
            return {"timestamps": []}
        if name in ("Summary", "SagaSummary", "EntitySummary"):
            return {"summary": "canned summary"}
        if name in ("SummaryDescription",):
            return {"description": "canned description"}
        if name in ("SummarizedEntities",):
            return {"summaries": []}
        return {}


class _HashingEmbedder(EmbedderClient):
    """Deterministic offline embedder (copied from the integration doubles)."""

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


def _make_config(tmp_path):
    """Hermetic config: temp home + embedded Ladybug graph, isolated from the real env."""
    graph_path = tmp_path / "graph.db"
    return load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "ladybug"},
    )


def test_each_episode_carries_its_own_repo_and_agent(tmp_path) -> None:
    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=_MockLLMClient(["acme", "globex", "postgres", "redis"]),
            embedder=_HashingEmbedder(),
        )
        try:
            # Note both facts into the SAME namespace with distinct (repo, agent).
            for repo, agent, fact in FACTS:
                await engine.note(fact, NAMESPACE, repo, source=agent)

            # Read the episodes back off the real graph and inspect provenance directly.
            episodes = await EpisodicNode.get_by_group_ids(engine._driver, [NAMESPACE])
            descriptions = {episode.source_description for episode in episodes}

            assert descriptions == {
                "repo=acme/widgets agent=copilot",
                "repo=globex/gadgets agent=claude",
            }, f"each episode must land its own parseable source_description: {descriptions!r}"

            # And the structured form is parseable back into (repo, agent) pairs.
            parsed = {
                tuple(token.split("=", 1)[1] for token in desc.split(" ")) for desc in descriptions
            }
            assert parsed == {("acme/widgets", "copilot"), ("globex/gadgets", "claude")}
        finally:
            await engine.close()

    asyncio.run(scenario())
