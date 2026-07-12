"""Local LLM strategy (E4-S7 / #64).

A fully *local* extraction model behind the same ``LLMClient`` seam, so
entity/edge extraction runs offline with **no API key and no network** for
agents that have no borrowable host model. The required concrete backend is
Ollama's chat API (``POST {base_url}/api/chat``, default
``http://localhost:11434``); a llama.cpp OpenAI-compatible
``/v1/chat/completions`` variant is a small future addition behind the same
:class:`LocalBackend` seam.

Design (mirrors the other strategies):

- Construction is cheap and **never touches the network** — it only records the
  base URL / model and builds an :class:`OllamaBackend` (a bare URL holder). The
  HTTP request happens lazily inside :meth:`LocalLLMClient._generate_response`,
  so ``select_llm_client`` / engine construction / ``search()`` / ``health()``
  all keep working with nothing running; only an actual extraction call reaches
  localhost.
- **Schema-in-prompt JSON parsing**: when graphiti passes a pydantic
  ``response_model``, its JSON schema is embedded in the prompt and the model's
  JSON reply is parsed back into the dict graphiti expects — the same technique
  borrow-host / byo-key use, which needs no provider-native structured-output
  mode and therefore works against a plain local model.
- The only part that touches the outside world is the tiny :class:`LocalBackend`
  ``chat`` seam, which is exactly why tests fake it (or monkeypatch
  ``urllib``) and never spawn a real Ollama.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Protocol, runtime_checkable

from graphiti_core.llm_client.client import LLMClient, ModelSize
from graphiti_core.llm_client.config import LLMConfig as GraphitiLLMConfig
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_LOCAL_MODEL = "llama3.1"
DEFAULT_MAX_TOKENS = 16384
#: Generous ceiling for a single local generation; only used by the real HTTP call.
DEFAULT_TIMEOUT_S = 300.0


class LocalLLMError(RuntimeError):
    """Raised when the local model cannot produce a usable (JSON) completion."""


@runtime_checkable
class LocalBackend(Protocol):
    """Seam over a single local chat completion.

    Implementations take Ollama-style ``{"role", "content"}`` messages plus the
    model name / token budget and return the model's raw text response. This is
    the only part of the local strategy that touches the network, which is
    precisely why it is a tiny, fakeable protocol.
    """

    async def chat(self, messages: list[dict[str, str]], *, model: str, max_tokens: int) -> str: ...


class OllamaBackend:
    """Concrete :class:`LocalBackend` for a local Ollama server.

    Talks to ``POST {base_url}/api/chat`` with ``stream=false`` using only the
    Python standard library (``urllib``) so the local path needs no extra
    dependency. ``format="json"`` nudges Ollama to emit syntactically valid JSON,
    which complements — but does not replace — the schema-in-prompt instruction
    that :class:`LocalLLMClient` embeds. The blocking request is dispatched via
    :func:`asyncio.to_thread` so it is safe to ``await`` from graphiti's async
    extraction path.
    """

    def __init__(self, base_url: str | None = None, *, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self._base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return self._base_url

    async def chat(self, messages: list[dict[str, str]], *, model: str, max_tokens: int) -> str:
        return await asyncio.to_thread(self._post_chat, messages, model, max_tokens)

    def _post_chat(self, messages: list[dict[str, str]], model: str, max_tokens: int) -> str:
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                # Constrain output to valid JSON syntax; the *shape* is still driven
                # by the schema embedded in the prompt (schema-in-prompt).
                "format": "json",
                "options": {"temperature": 0, "num_predict": max_tokens},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            # Offline / server down: fail loud and actionable at call time (never at
            # construction), so the engine still builds with no Ollama running.
            raise LocalLLMError(
                f"local Ollama request to {self._base_url}/api/chat failed: {exc}. "
                "Is `ollama serve` running and the model pulled?"
            ) from exc
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise LocalLLMError(f"local Ollama returned a non-JSON envelope: {exc}") from exc
        message = payload.get("message") if isinstance(payload, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not content:
            raise LocalLLMError("local Ollama returned an empty message content")
        return content


def _schema_instruction(response_model: type[BaseModel]) -> str:
    schema = json.dumps(response_model.model_json_schema(), indent=2)
    return (
        "Respond with a SINGLE JSON object and nothing else — no prose, no code "
        "fences, no explanation. The object MUST validate against this JSON "
        f"schema:\n{schema}"
    )


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop the opening fence line (``` or ```json) and the trailing fence.
    without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if "```" in without_open:
        without_open = without_open.rsplit("```", 1)[0]
    return without_open.strip()


def _loads_json_object(raw: str) -> dict[str, Any]:
    """Best-effort parse of a JSON object out of a raw model response."""
    candidate = _strip_code_fences(raw)
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError):
        # Fall back to the outermost {...} span if the model added stray text.
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found in local model response") from None
        parsed = json.loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("local model response JSON was not an object")
    return parsed


class LocalLLMClient(LLMClient):
    """graphiti ``LLMClient`` backed by a local model + schema-in-prompt JSON parse.

    ``base_url`` / ``model`` come from :class:`memrelay.config.LLMConfig`
    (``local_base_url`` / ``local_model``); ``backend`` is injectable purely so
    tests stay hermetic. Constructing this is cheap and does not open any socket.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        backend: LocalBackend | None = None,
        config: GraphitiLLMConfig | None = None,
        max_json_retries: int = 2,
    ) -> None:
        model_name = model or DEFAULT_LOCAL_MODEL
        # Record the model up front for the base class's retry/logging, but do not
        # open any connection here.
        super().__init__(config or GraphitiLLMConfig(model=model_name), cache=False)
        self._base_url = base_url or DEFAULT_OLLAMA_BASE_URL
        self._model = model_name
        self._backend = backend or OllamaBackend(self._base_url)
        self._max_json_retries = max_json_retries

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model_name(self) -> str:
        return self._model

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
        base_messages = self._build_messages(messages, response_model)

        attempt_messages = base_messages
        last_error: Exception | None = None
        for attempt in range(self._max_json_retries + 1):
            raw = await self._backend.chat(
                attempt_messages, model=self._model, max_tokens=max_tokens
            )
            try:
                return _loads_json_object(raw)
            except ValueError as exc:
                last_error = exc
                logger.debug("local model JSON parse failed (attempt %d): %s", attempt + 1, exc)
                attempt_messages = base_messages + [
                    {
                        "role": "user",
                        "content": (
                            f"Your previous reply was not valid JSON ({exc}). "
                            "Reply again with ONLY the JSON object."
                        ),
                    }
                ]
        raise LocalLLMError(
            f"local model at {self._base_url} did not return valid JSON after "
            f"{self._max_json_retries + 1} attempts: {last_error}"
        )
