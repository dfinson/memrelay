"""Runnable demo of the memrelay memory engine: note -> recall on embedded Kuzu.

Hermetic and offline by default: it uses a deterministic in-process mock LLM
(so no Copilot subprocess / API key is required) and the real ``LocalEmbedder``,
against a throwaway temp Kuzu database — it never touches ``~/.memrelay``.

Run it::

    python scripts/engine_demo.py

This is intentionally a script (not a CLI subcommand): the CLI/daemon are owned
by a parallel workstream and will inject ``MemoryEngine`` for real later.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

# The engine is imported from its submodule (not a top-level export) exactly as
# the daemon will import it later.
from memrelay.config import load_config
from memrelay.engine.graphiti import MemoryEngine
from memrelay.mcp.format import format_as_map, format_detail

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
    pass

NAMESPACE = "demo-project"
FACTS = [
    "memrelay stores its persistent agent memory in an embedded Kuzu graph database.",
    "The memory engine embeds text locally with the BAAI/bge-small-en-v1.5 model.",
]
QUERY = "which graph database does memrelay use for memory"


def _build_mock_llm():
    """A deterministic extractor so the demo needs no LLM backend."""
    from graphiti_core.llm_client.client import LLMClient, ModelSize
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.prompts.models import Message

    vocab = ["memrelay", "Kuzu", "BAAI/bge-small-en-v1.5"]

    class DemoLLM(LLMClient):
        def __init__(self) -> None:
            super().__init__(LLMConfig(), cache=False)

        async def _generate_response(
            self,
            messages: list[Message],
            response_model: Any = None,
            max_tokens: int = 16384,
            model_size: ModelSize = ModelSize.medium,
        ) -> dict[str, Any]:
            name = response_model.__name__ if response_model else None
            text = "\n".join(str(m.content) for m in messages).lower()
            found = [v for v in vocab if v.lower() in text]
            if name == "ExtractedEntities":
                return {
                    "extracted_entities": [
                        {"name": v, "entity_type_id": 0, "episode_indices": [0]} for v in found
                    ]
                }
            if name == "ExtractedEdges":
                if len(found) < 2:
                    return {"edges": []}
                return {
                    "edges": [
                        {
                            "source_entity_name": found[0],
                            "target_entity_name": found[1],
                            "relation_type": "RELATES_TO",
                            "fact": f"{found[0]} uses {found[1]}",
                            "valid_at": None,
                            "invalid_at": None,
                            "episode_indices": [0],
                        }
                    ]
                }
            if name in ("EdgeTimestamps", "EdgeDates"):
                return {"valid_at": None, "invalid_at": None}
            if name == "BatchEdgeTimestamps":
                return {"timestamps": []}
            if name == "NodeResolutions":
                return {
                    "entity_resolutions": [
                        {"id": i, "name": v, "duplicate_candidate_id": -1}
                        for i, v in enumerate(found)
                    ]
                }
            if name == "EdgeDuplicate":
                return {"duplicate_facts": [], "contradicted_facts": []}
            if name in ("Summary", "SagaSummary", "EntitySummary"):
                return {"summary": "demo summary"}
            if name == "SummaryDescription":
                return {"description": "demo description"}
            if name == "SummarizedEntities":
                return {"summaries": []}
            return {}

    return DemoLLM()


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="memrelay_demo_"))
    cfg = load_config(
        environ={},
        home=str(tmp),
        graph={"path": str(tmp / "graph.db"), "backend": "kuzu"},
    )
    print(f"Temp Kuzu graph: {cfg.graph_path}")

    engine = await MemoryEngine.from_config(cfg, llm_client=_build_mock_llm())
    try:
        for fact in FACTS:
            note_id = await engine.note(fact, namespace=NAMESPACE, repo="memrelay")
            print(f"  noted -> {note_id}")

        print(f"\nRecall query: {QUERY!r}")
        results = await engine.search(QUERY, namespace=NAMESPACE, prefer_repo="memrelay")
        # ``results`` is the daemon wire schema {"nodes", "edges", "scores"}; render
        # it with the real daemon formatter to demonstrate the (later) one-line swap.
        print(format_as_map(results))

        if results["nodes"]:
            detail = await engine.detail(results["nodes"][0]["uuid"], namespace=NAMESPACE)
            print("\n--- detail of top node ---")
            print(format_detail(detail))

        health = await engine.health()
        print(f"\nhealth: {health}")
    finally:
        await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
