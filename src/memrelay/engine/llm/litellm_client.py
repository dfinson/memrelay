"""In-process LiteLLM SDK LLM strategy (``strategy = "litellm"``).

Wraps the `litellm <https://docs.litellm.ai>`_ Python SDK **in-process** behind
graphiti-core's ``LLMClient`` so entity/edge extraction can run against *any*
LiteLLM-supported provider — **with no external proxy or sidecar**. The concrete
motivating target is Azure AI Foundry ``gpt-4.1-mini`` as a cheap, non-looping
extraction model (``model = "azure/gpt-4.1-mini"``); the same adapter reaches
OpenAI, Bedrock, Vertex, Ollama, and ~100 other providers just by changing the
``model`` string.

Design (mirrors :mod:`memrelay.engine.llm.local`):

- Construction is cheap and **never touches the network** — it only records the
  model string / key-env name and builds a :class:`_SdkLiteLLMBackend` (a bare
  holder). ``litellm`` itself is a heavy import, so it is imported **lazily inside
  the backend method**, never at module top level; that keeps CLI startup / engine
  construction / ``search()`` / ``health()`` fast and working with no LLM present.
  The first network call happens inside :meth:`LiteLLMLLMClient._generate_response`.
- **Schema-in-prompt JSON**: graphiti's ``response_model`` JSON schema is embedded
  in the prompt and the model's JSON reply is parsed back into the dict graphiti
  expects (shared :mod:`memrelay.engine.llm._json` helpers). This is provider-
  agnostic and needs no provider-native structured-output mode, so it works
  uniformly for Azure / Bedrock / Vertex / Ollama.
- **Fail loud at call time, never at construction.** A missing model string or a
  named-but-unset ``api_key_env`` raises :class:`LiteLLMConfigError` when extraction
  is actually invoked (mirroring byo-key's ``ByoKeyConfigError``); the engine still
  builds when litellm is requested but unconfigured.
- The only part that touches the outside world is the tiny :class:`LiteLLMBackend`
  ``acomplete`` seam, which is exactly why tests fake it (or monkeypatch
  ``litellm.acompletion``) and never reach a real provider.

Credentials: set ``api_key_env`` to pass an explicit key through to litellm, or
leave it unset and let litellm read the provider's native environment variables
(Azure: ``AZURE_API_KEY`` / ``AZURE_API_BASE`` / ``AZURE_API_VERSION``).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

from graphiti_core.llm_client.client import LLMClient, ModelSize
from graphiti_core.llm_client.config import LLMConfig as GraphitiLLMConfig
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from ._json import _loads_json_object, _schema_instruction

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 16384


class LiteLLMError(RuntimeError):
    """Raised when the litellm call fails or cannot produce a usable (JSON) completion."""


class LiteLLMConfigError(RuntimeError):
    """Raised when litellm config is incomplete (no model, or the key env is unset)."""


def _extract_content(response: Any) -> str:
    """Pull the assistant text out of a litellm/OpenAI-compatible completion response."""
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise LiteLLMError(f"litellm returned an unexpected response shape: {exc}") from exc
    if not content:
        raise LiteLLMError("litellm returned empty message content")
    return content


@runtime_checkable
class LiteLLMBackend(Protocol):
    """Seam over a single litellm chat completion.

    Implementations take OpenAI-style ``{"role", "content"}`` messages plus the
    litellm model string, token budget and an optional explicit API key, and return
    the model's raw text response. This is the only part of the litellm strategy
    that touches the network, which is precisely why it is a tiny, fakeable protocol.
    """

    async def acomplete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        api_key: str | None,
    ) -> str: ...


class _SdkLiteLLMBackend:
    """Concrete :class:`LiteLLMBackend` over ``litellm.acompletion`` (in-process).

    ``litellm`` is imported **lazily inside** :meth:`acomplete` — it is a heavy
    import, and deferring it preserves fast CLI/engine startup and the "engine
    builds with no LLM present" invariant. ``temperature=0`` keeps extraction
    deterministic; the output *shape* is driven by the schema embedded in the prompt.
    """

    async def acomplete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        api_key: str | None,
    ) -> str:
        import litellm  # lazy: heavy import, kept out of module import / engine build

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if api_key:
            kwargs["api_key"] = api_key
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any provider error loudly + actionably
            raise LiteLLMError(
                f"litellm completion for model {model!r} failed: {exc}. "
                "Check the model string (e.g. 'azure/<deployment>') and that the provider's "
                "credentials/endpoint env vars are set (Azure: AZURE_API_KEY / AZURE_API_BASE / "
                "AZURE_API_VERSION)."
            ) from exc
        return _extract_content(response)


class LiteLLMLLMClient(LLMClient):
    """graphiti ``LLMClient`` backed by the litellm SDK + schema-in-prompt JSON parse.

    ``model`` is a litellm model string (``azure/<deployment>``, ``openai/gpt-4.1-mini``,
    ``bedrock/...``, ``ollama/llama3.1``, ...); ``api_key_env`` optionally names an
    environment variable whose value is passed to litellm as the API key (otherwise
    litellm reads the provider's native env vars). ``backend`` is injectable purely so
    tests stay hermetic. Constructing this is cheap and does not import litellm or open
    any socket.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        backend: LiteLLMBackend | None = None,
        config: GraphitiLLMConfig | None = None,
        max_json_retries: int = 2,
    ) -> None:
        # Record the model up front for the base class's retry/logging, but do not
        # import litellm, read the key, or open any connection here.
        super().__init__(config or GraphitiLLMConfig(model=model), cache=False)
        self._model = model
        self._api_key_env = api_key_env
        self._backend = backend or _SdkLiteLLMBackend()
        self._max_json_retries = max_json_retries

    @property
    def model_name(self) -> str | None:
        return self._model

    @property
    def api_key_env(self) -> str | None:
        return self._api_key_env

    def _resolve_model(self) -> str:
        if not self._model:
            raise LiteLLMConfigError(
                "litellm strategy requires llm.model to be set to a litellm model string "
                "(e.g. 'azure/gpt-4.1-mini', 'openai/gpt-4.1-mini', 'bedrock/...', "
                "'ollama/llama3.1')."
            )
        return self._model

    def _resolve_api_key(self) -> str | None:
        env_name = self._api_key_env
        if not env_name:
            # No explicit key env configured: let litellm read the provider's native
            # credentials from the environment (Azure: AZURE_API_KEY / AZURE_API_BASE /
            # AZURE_API_VERSION; OpenAI: OPENAI_API_KEY; etc.).
            return None
        key = os.environ.get(env_name)
        if not key:
            raise LiteLLMConfigError(
                f"litellm strategy: environment variable {env_name!r} (llm.api_key_env) is not set"
            )
        return key

    def _build_messages(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None,
    ) -> list[dict[str, str]]:
        payload = [{"role": message.role, "content": message.content} for message in messages]
        if response_model is None:
            return payload
        instruction = _schema_instruction(response_model)
        if payload:
            # Append to the last turn so the schema is the final thing the model reads.
            payload[-1] = {
                "role": payload[-1]["role"],
                "content": f"{payload[-1]['content']}\n\n{instruction}",
            }
        else:
            payload.append({"role": "user", "content": instruction})
        return payload

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        # Resolve config lazily at call time so construction never fails (engine still
        # builds when litellm is requested but unconfigured); these raise loud, clear
        # errors only when extraction is actually invoked.
        model = self._resolve_model()
        api_key = self._resolve_api_key()
        base_messages = self._build_messages(messages, response_model)

        attempt_messages = base_messages
        last_error: Exception | None = None
        for attempt in range(self._max_json_retries + 1):
            raw = await self._backend.acomplete(
                attempt_messages, model=model, max_tokens=max_tokens, api_key=api_key
            )
            try:
                return _loads_json_object(raw)
            except ValueError as exc:
                last_error = exc
                logger.debug("litellm JSON parse failed (attempt %d): %s", attempt + 1, exc)
                attempt_messages = base_messages + [
                    {
                        "role": "user",
                        "content": (
                            f"Your previous reply was not valid JSON ({exc}). "
                            "Reply again with ONLY the JSON object."
                        ),
                    }
                ]
        raise LiteLLMError(
            f"litellm model {model!r} did not return valid JSON after "
            f"{self._max_json_retries + 1} attempts: {last_error}"
        )
