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
from memrelay.engine.graphiti import MemoryEngine, _episode_agent, _episode_repo

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


def test_note_roundtrips_space_and_equals_in_repo_and_agent(tmp_path) -> None:
    """A repo/agent carrying a space or ``=`` survives note() → stored sd → inverse parsers.

    This is the data-correctness core of the fix: SPEC §5.3 names ``claude code`` (a
    space-containing agent id) as a first-class actor, and a hostile/odd git origin can push a
    space or ``=`` into ``repo``. Both must round-trip losslessly instead of forging/splitting
    the token grammar.
    """

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=_MockLLMClient(["postgres"]),
            embedder=_HashingEmbedder(),
        )
        try:
            await engine.note(
                "The my-org service uses postgres.",
                NAMESPACE,
                "my org/name=v2",  # a space AND an '=' in the repo
                source="claude code",  # a space in the agent id
            )
            episodes = await EpisodicNode.get_by_group_ids(engine._driver, [NAMESPACE])
            assert len(episodes) == 1
            sd = episodes[0].source_description
            # Stored wire form is percent-escaped so it stays a clean two-token string.
            assert sd == "repo=my%20org/name%3Dv2 agent=claude%20code"
            # And it round-trips back through the inverse parsers the destructive ops rely on.
            assert _episode_repo(sd) == "my org/name=v2"
            assert _episode_agent(sd) == "claude code"
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_note_forge_attempt_via_repo_does_not_hijack_agent(tmp_path) -> None:
    """A repo value that tries to smuggle an ``agent=`` token cannot rewrite the real agent."""

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=_MockLLMClient(["postgres"]),
            embedder=_HashingEmbedder(),
        )
        try:
            await engine.note(
                "The service uses postgres.",
                NAMESPACE,
                "owner/name agent=admin",  # hostile: tries to inject agent=admin
                source="copilot",
            )
            episodes = await EpisodicNode.get_by_group_ids(engine._driver, [NAMESPACE])
            sd = episodes[0].source_description
            # The real agent survives; the injected token is inert (escaped into the repo).
            assert _episode_agent(sd) == "copilot"
            assert _episode_repo(sd) == "owner/name agent=admin"
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_forget_repo_matches_space_containing_repo(tmp_path) -> None:
    """``forget --repo`` (destructive) selects a space-containing repo — pre-fix it matched zero.

    Drives the real selection path (``_forget_repo`` parses each episode's ``source_description``
    via ``_episode_repo``) to prove the round-trip closes the data-retention hole end to end.
    """

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=_MockLLMClient(["postgres", "redis"]),
            embedder=_HashingEmbedder(),
        )
        try:
            await engine.note("acme uses postgres.", NAMESPACE, "my org/name", source="copilot")
            await engine.note("globex uses redis.", NAMESPACE, "other/clean", source="copilot")

            # Dry-run selects exactly the space-containing repo's episode (pre-fix: zero),
            # and matching stays case-insensitive after decoding.
            assert await engine._forget_repo("my org/name", dry_run=True) == 1
            assert await engine._forget_repo("MY ORG/NAME", dry_run=True) == 1
            assert await engine._forget_repo("other/clean", dry_run=True) == 1

            # Actually forget the space repo; only its episode is removed, the clean one stays.
            assert await engine._forget_repo("my org/name", dry_run=False) == 1
            remaining = {
                _episode_repo(episode.source_description)
                for episode in await EpisodicNode.get_by_group_ids(engine._driver, [NAMESPACE])
            }
            assert remaining == {"other/clean"}
        finally:
            await engine.close()

    asyncio.run(scenario())
