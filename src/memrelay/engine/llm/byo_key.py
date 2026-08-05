"""Bring-your-own-key LLM strategy (E4-S4 / #38).

``ByoKeyLLMClient`` wraps graphiti-core's native ``OpenAIClient`` (which uses
the provider's structured/JSON output mode) but constructs it *lazily*: nothing
reads the API key or touches the network until the first ``generate_response``
call. That keeps CI hermetic — the engine can be built for ``search()`` /
``health()`` with no key present, and only ``note()`` (entity extraction) will
raise a clear error if the key is genuinely missing.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from graphiti_core.llm_client.client import LLMClient, ModelSize
from graphiti_core.llm_client.config import LLMConfig as GraphitiLLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

if TYPE_CHECKING:
    from graphiti_core.embedder.openai import OpenAIEmbedder
    from graphiti_core.tracer import Tracer

    from memrelay.config import Config

DEFAULT_OPENAI_EMBEDDING_DIM = 1536


class ByoKeyConfigError(RuntimeError):
    """Raised when byo-key config is incomplete or the API key is unavailable."""


def _require_env(env_name: str | None, *, purpose: str) -> str:
    if not env_name:
        raise ByoKeyConfigError(f"{purpose}: no api_key_env configured")
    key = os.environ.get(env_name)
    if not key:
        raise ByoKeyConfigError(f"{purpose}: environment variable {env_name!r} is not set")
    return key


class ByoKeyLLMClient(LLMClient):
    """Lazy graphiti ``LLMClient`` over the provider's native JSON mode."""

    def __init__(self, cfg: Config) -> None:
        # Record the model up front so base-class retry logic has it, but do not
        # build the network client or read the key yet.
        super().__init__(GraphitiLLMConfig(model=cfg.llm.model), cache=False)
        self._cfg = cfg
        self._delegate: OpenAIClient | None = None

    def _build_delegate(self) -> OpenAIClient:
        key = _require_env(self._cfg.llm.api_key_env, purpose="byo-key LLM")
        graphiti_config = GraphitiLLMConfig(
            api_key=key,
            model=self._cfg.llm.model,
            base_url=os.environ.get("OPENAI_BASE_URL"),
        )
        return OpenAIClient(config=graphiti_config)

    def _get_delegate(self) -> OpenAIClient:
        if self._delegate is None:
            self._delegate = self._build_delegate()
            # ``set_tracer`` may have been called before the delegate existed
            # (graphiti wires it at ``Graphiti`` construction, we build lazily).
            self._delegate.set_tracer(self.tracer)
        self.token_tracker = self._delegate.token_tracker
        return self._delegate

    def set_tracer(self, tracer: Tracer) -> None:
        """Keep the delegate's tracer in step with ours.

        ``generate_response`` delegates rather than running the base implementation,
        so ``self.tracer`` is never read on the call path — without this override the
        tracer graphiti installs would land on the wrapper while the delegate kept its
        own ``NoOpTracer``, and every span would be silently dropped.
        """
        super().set_tracer(tracer)
        if self._delegate is not None:
            self._delegate.set_tracer(tracer)

    async def generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        model_size: ModelSize = ModelSize.medium,
        group_id: str | None = None,
        prompt_name: str | None = None,
        *,
        attribute_extraction: bool = False,
    ) -> dict[str, Any]:
        """Delegate through graphiti's public API.

        graphiti's provider-specific low-level return contract changed across the
        supported 0.29.x line. Its public method owns that adaptation plus retry,
        tracing, token accounting, and prompt preparation, so bypassing it can leak
        a provider tuple into extraction instead of the final response mapping.
        """
        return await self._get_delegate().generate_response(
            messages,
            response_model=response_model,
            max_tokens=max_tokens,
            model_size=model_size,
            group_id=group_id,
            prompt_name=prompt_name,
            attribute_extraction=attribute_extraction,
        )

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 16384,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        """Satisfy the base-class provider seam without using a private delegate API."""
        return await self._get_delegate().generate_response(
            messages,
            response_model=response_model,
            max_tokens=max_tokens,
            model_size=model_size,
        )


def build_openai_embedder(cfg: Config) -> OpenAIEmbedder:
    """Build graphiti's OpenAI embedder for byo-key embeddings (text-embedding-3-small).

    Only called when ``cfg.embeddings.provider == 'openai'``; requires the key to
    be present, so it is never invoked on the key-less default path.
    """
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

    key = _require_env(cfg.embeddings.api_key_env, purpose="byo-key embeddings")
    embedder_config = OpenAIEmbedderConfig(
        embedding_model=cfg.embeddings.model,
        embedding_dim=DEFAULT_OPENAI_EMBEDDING_DIM,
        api_key=key,
        base_url=os.environ.get("OPENAI_BASE_URL"),
    )
    return OpenAIEmbedder(config=embedder_config)
