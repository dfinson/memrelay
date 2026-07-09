"""Offline precision@k harness over the real ``engine.search`` path (E11-S4 / #21).

Builds a real embedded-Ladybug :class:`MemoryEngine` (like the hermetic integration
e2e tests), but injects two deterministic doubles so the whole thing is reproducible
and needs no API key and no embedding-model download:

* extraction  -> an in-process mock LLM (fixed, schema-conformant responses);
* embeddings  -> :class:`DeterministicEmbedder` (fixed hashed bag-of-words).

It then notes the generated synthetic-session facts, runs each labeled gold query
through the *real* ``engine.search``, and computes macro precision@k / hit@k over the
STRUCTURED ``{"nodes": ...}`` result. What this measures — and why it is a stable
regression gate rather than a semantic-quality score — is documented in
``tests/eval/README.md``.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from _embedder import DeterministicEmbedder
from _generator import Corpus, generate
from _precision import ranked_identities, summarize
from graphiti_core.llm_client.client import LLMClient, ModelSize
from graphiti_core.llm_client.config import LLMConfig as GraphitiLLMConfig
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from memrelay.config import load_config
from memrelay.engine.graphiti import MemoryEngine

# Single-sourced defaults so the generator, the baseline artifact, the pytest gate,
# and the CI checker can never drift apart.
DEFAULT_SEED = 1729
DEFAULT_N_TOPICS = 12
DEFAULT_FACTS_PER_TOPIC = 2
DEFAULT_KS: tuple[int, ...] = (1, 3, 5)
#: Identifier recorded in the baseline so a future embedder swap is visible in the diff.
EMBEDDER_ID = "hashed-bow-384"


def _canned(model_name: str | None, found: list[str]) -> dict[str, Any]:
    """Deterministic, schema-conformant responses keyed by graphiti prompt model.

    ``found`` are the vocabulary entities detected in the episode text, so the mock
    behaves like a perfect extractor without hard-coding any single scenario. This is
    the harness-owned twin of ``tests/integration/conftest.py``'s mock — copied on
    purpose so the eval never imports test fixtures.
    """
    if model_name == "ExtractedEntities":
        return {
            "extracted_entities": [
                {"name": name, "entity_type_id": 0, "episode_indices": [0]} for name in found
            ]
        }
    if model_name == "CombinedExtraction":
        return {
            "extracted_entities": [{"name": name, "entity_type_id": 0} for name in found],
            "edges": _edges(found),
        }
    if model_name == "ExtractedEdges":
        return {"edges": _edges(found)}
    if model_name == "NodeResolutions":
        return {
            "entity_resolutions": [
                {"id": index, "name": name, "duplicate_candidate_id": -1}
                for index, name in enumerate(found)
            ]
        }
    if model_name == "EdgeDuplicate":
        return {"duplicate_facts": [], "contradicted_facts": []}
    if model_name in ("EdgeTimestamps", "EdgeDates"):
        return {"valid_at": None, "invalid_at": None}
    if model_name == "BatchEdgeTimestamps":
        return {"timestamps": []}
    if model_name in ("Summary", "SagaSummary", "EntitySummary"):
        return {"summary": "canned summary"}
    if model_name == "SummaryDescription":
        return {"description": "canned description"}
    if model_name == "SummarizedEntities":
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
            "fact": f"{source} relates to {target}",
            "valid_at": None,
            "invalid_at": None,
            "episode_indices": [0],
        }
    ]


class MockLLM(LLMClient):
    """Deterministic, in-process ``LLMClient`` for hermetic extraction (no network)."""

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


def _hermetic_config(home: Path):
    """Temp home + embedded Ladybug, fully isolated from the caller's real environment."""
    graph_path = home / "graph.db"
    return load_config(
        environ={},
        home=str(home),
        graph={"path": str(graph_path), "backend": "ladybug"},
    )


def _quiet_dependency_logging() -> None:
    """Silence graphiti's deterministic mock-dedup warnings so runner output is readable.

    Driving graphiti's dedup with a perfect mock LLM makes it log (deterministic) "invalid
    dedupe id" warnings on every episode. They do not affect ranking or determinism — the
    eval only reads the ranked ``nodes`` — so we keep the runner focused on the metrics.
    """
    logging.getLogger("graphiti_core").setLevel(logging.ERROR)


async def measure(home: Path, corpus: Corpus, ks: tuple[int, ...]) -> dict[str, float]:
    """Note every fact, run each gold query through ``engine.search``, return p@k/hit@k."""
    cfg = _hermetic_config(home)
    engine = await MemoryEngine.from_config(
        cfg,
        llm_client=MockLLM(list(corpus.vocab)),
        embedder=DeterministicEmbedder(),
    )
    try:
        for fact in corpus.all_facts():
            await engine.note(fact, namespace=corpus.namespace)

        results: list[tuple[list[str], tuple[str, ...]]] = []
        for gold in corpus.queries:
            recall = await engine.search(gold.query, namespace=corpus.namespace)
            results.append((ranked_identities(recall["nodes"]), gold.relevant))
        return summarize(results, ks)
    finally:
        await engine.close()


def run_eval(
    *,
    seed: int = DEFAULT_SEED,
    n_topics: int = DEFAULT_N_TOPICS,
    facts_per_topic: int = DEFAULT_FACTS_PER_TOPIC,
    ks: tuple[int, ...] = DEFAULT_KS,
) -> dict[str, Any]:
    """Generate the corpus, build the engine in a throwaway home, and measure precision@k.

    Returns a structured report (metrics + reproducibility metadata) suitable for
    writing to / checking against the baseline artifact.
    """
    _quiet_dependency_logging()
    corpus = generate(seed=seed, n_topics=n_topics, facts_per_topic=facts_per_topic)
    with tempfile.TemporaryDirectory(prefix="memrelay-eval-") as tmp:
        metrics = asyncio.run(measure(Path(tmp), corpus, ks))
    return {
        "metrics": metrics,
        "config": {
            "seed": seed,
            "n_topics": n_topics,
            "facts_per_topic": facts_per_topic,
            "namespace": corpus.namespace,
            "ks": list(ks),
            "embedder": EMBEDDER_ID,
            "num_sessions": len(corpus.sessions),
            "num_facts": len(corpus.all_facts()),
            "num_queries": len(corpus.queries),
        },
    }
