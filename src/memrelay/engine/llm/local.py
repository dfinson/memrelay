"""Local LLM strategy stub (E4-S7 / #64) — deferred.

A fully local extraction model (Ollama / llama.cpp) behind the same
``LLMClient`` seam is tracked as E4-S7 (#64) and intentionally NOT implemented in
this PR. This stub exists so the strategy registry has a concrete, importable
entry; it constructs cheaply and only raises when actually asked to generate,
which lets the fallback chain treat "local" as unavailable without special
casing.
"""

from __future__ import annotations

from typing import Any

from graphiti_core.llm_client.client import LLMClient, ModelSize
from graphiti_core.prompts.models import Message
from pydantic import BaseModel


class LocalLLMClient(LLMClient):
    """Placeholder client for the deferred local strategy (#64)."""

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 16384,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "The local LLM strategy (Ollama/llama.cpp) is deferred — see "
            "E4-S7 (#64). Use strategy='borrow-host' or 'byo-key'."
        )
