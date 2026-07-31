"""Unit tests for the in-process litellm SDK strategy (``strategy = "litellm"``).

Fully hermetic: the network is never touched and a real provider is never called.
Fast paths inject a fake :class:`LiteLLMBackend`; the real :class:`_SdkLiteLLMBackend`
wiring is exercised by monkeypatching ``litellm.acompletion``. Async coroutines are
driven with ``asyncio.run`` (matching test_local_llm.py / test_llm_strategy.py) so the
suite needs no pytest-asyncio plugin.
"""

from __future__ import annotations

import asyncio

import pytest
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from memrelay.config import load_config
from memrelay.engine.llm.litellm_client import (
    LiteLLMConfigError,
    LiteLLMError,
    LiteLLMLLMClient,
    _SdkLiteLLMBackend,
)
from memrelay.engine.llm.strategy import (
    STRATEGY_LITELLM,
    LiteLLMStrategy,
    select_llm_client,
)


class _ExtractedNode(BaseModel):
    name: str
    summary: str


class _RecordingBackend:
    """Fake :class:`LiteLLMBackend` that records calls and replays canned replies.

    Once the canned replies are exhausted the last reply repeats, which makes
    "always returns junk" (retries-exhausted) cases trivial to express.
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, object]] = []

    async def acomplete(self, messages, *, model: str, max_tokens: int, api_key: str | None) -> str:
        self.calls.append(
            {"messages": messages, "model": model, "max_tokens": max_tokens, "api_key": api_key}
        )
        index = min(len(self.calls) - 1, len(self._replies) - 1)
        return self._replies[index]


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    """Minimal OpenAI/litellm-compatible completion response stand-in."""

    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


def _messages() -> list[Message]:
    return [
        Message(role="system", content="Extract entities."),
        Message(role="user", content="Alice is a person."),
    ]


# ── construction is cheap / no network, no litellm import, no key ────────────────


def test_client_construction_is_cheap_and_offline():
    # No backend/key given: nothing is imported or dialed.
    client = LiteLLMLLMClient(model="azure/gpt-4.1-mini")
    assert client.model_name == "azure/gpt-4.1-mini"
    assert isinstance(client._backend, _SdkLiteLLMBackend)


def test_strategy_build_client_threads_model_and_key_env():
    cfg = load_config(
        environ={},
        llm={"strategy": "litellm", "model": "azure/gpt-4.1-mini", "api_key_env": "AZURE_API_KEY"},
    )
    client = LiteLLMStrategy().build_client(cfg)
    assert isinstance(client, LiteLLMLLMClient)
    assert client.model_name == "azure/gpt-4.1-mini"
    assert client.api_key_env == "AZURE_API_KEY"


# ── (a) model string + params threaded into the litellm call ─────────────────────


def test_model_string_and_params_are_threaded_into_the_call(monkeypatch):
    monkeypatch.setenv("MEMRELAY_UT_KEY", "sk-not-real")
    backend = _RecordingBackend(['{"name": "Alice", "summary": "a person"}'])
    client = LiteLLMLLMClient(
        model="azure/gpt-4.1-mini", api_key_env="MEMRELAY_UT_KEY", backend=backend
    )

    result = asyncio.run(
        client._generate_response(_messages(), response_model=_ExtractedNode, max_tokens=512)
    )

    assert result == {"name": "Alice", "summary": "a person"}
    call = backend.calls[0]
    assert call["model"] == "azure/gpt-4.1-mini"
    assert call["max_tokens"] == 512
    assert call["api_key"] == "sk-not-real"


# ── (b) schema-in-prompt injected + JSON reply parsed ────────────────────────────


def test_schema_is_embedded_in_prompt_and_json_reply_is_parsed():
    backend = _RecordingBackend(['{"name": "Alice", "summary": "a person"}'])
    client = LiteLLMLLMClient(model="openai/gpt-4.1-mini", backend=backend)

    result = asyncio.run(client._generate_response(_messages(), response_model=_ExtractedNode))

    assert result == {"name": "Alice", "summary": "a person"}
    # The response_model's JSON schema must reach the model (schema-in-prompt).
    last_content = backend.calls[0]["messages"][-1]["content"]
    assert "summary" in last_content
    assert "properties" in last_content


def test_no_response_model_sends_no_schema_instruction():
    backend = _RecordingBackend(['{"ack": true}'])
    client = LiteLLMLLMClient(model="openai/gpt-4.1-mini", backend=backend)

    result = asyncio.run(client._generate_response(_messages()))

    assert result == {"ack": True}
    assert "JSON schema" not in backend.calls[0]["messages"][-1]["content"]


def test_code_fenced_json_is_stripped_and_parsed():
    backend = _RecordingBackend(['```json\n{"name": "Bo", "summary": "dev"}\n```'])
    client = LiteLLMLLMClient(model="ollama/llama3.1", backend=backend)

    result = asyncio.run(client._generate_response(_messages(), response_model=_ExtractedNode))

    assert result == {"name": "Bo", "summary": "dev"}


def test_json_object_recovered_from_surrounding_prose():
    backend = _RecordingBackend(['Sure! Here you go: {"name": "Cy", "summary": "qa"} — done.'])
    client = LiteLLMLLMClient(model="bedrock/anthropic.claude", backend=backend)

    result = asyncio.run(client._generate_response(_messages(), response_model=_ExtractedNode))

    assert result == {"name": "Cy", "summary": "qa"}


# ── bounded JSON-correction retry ────────────────────────────────────────────────


def test_retries_with_correction_then_succeeds():
    backend = _RecordingBackend(["not json at all", '{"ok": true}'])
    client = LiteLLMLLMClient(model="openai/x", backend=backend, max_json_retries=2)

    result = asyncio.run(client._generate_response(_messages()))

    assert result == {"ok": True}
    assert len(backend.calls) == 2
    assert "not valid JSON" in backend.calls[1]["messages"][-1]["content"]


def test_raises_litellm_error_after_retries_exhausted():
    backend = _RecordingBackend(["still not json"])  # repeated for every attempt
    client = LiteLLMLLMClient(model="openai/x", backend=backend, max_json_retries=2)

    with pytest.raises(LiteLLMError):
        asyncio.run(client._generate_response(_messages()))

    assert len(backend.calls) == 3  # initial + 2 retries


# ── (e) missing/invalid config raises loudly at call time, NOT at construction ───


def test_missing_key_raises_config_error_at_call_time_not_construction(monkeypatch):
    monkeypatch.delenv("MEMRELAY_UT_KEY", raising=False)
    # Construction must NOT raise even though the key env is unset...
    client = LiteLLMLLMClient(model="azure/gpt-4.1-mini", api_key_env="MEMRELAY_UT_KEY")
    # ...the loud, clear failure surfaces only at extraction/call time, and never
    # reaches the network (the default SDK backend would need litellm + a provider).
    with pytest.raises(LiteLLMConfigError) as excinfo:
        asyncio.run(client._generate_response(_messages(), response_model=_ExtractedNode))
    assert "MEMRELAY_UT_KEY" in str(excinfo.value)


def test_missing_model_raises_config_error_at_call_time():
    client = LiteLLMLLMClient()  # no model configured
    with pytest.raises(LiteLLMConfigError):
        asyncio.run(client._generate_response(_messages()))


def test_no_key_env_passes_none_and_lets_litellm_read_native_env():
    # api_key_env unset → api_key is None on the wire, so litellm reads the provider's
    # native env vars (Azure: AZURE_API_KEY / AZURE_API_BASE / AZURE_API_VERSION).
    backend = _RecordingBackend(['{"ok": true}'])
    client = LiteLLMLLMClient(model="azure/gpt-4.1-mini", backend=backend)

    asyncio.run(client._generate_response(_messages()))

    assert backend.calls[0]["api_key"] is None


# ── (c) requesting strategy="litellm" selects/builds the client (the trap) ───────


def test_requesting_litellm_selects_litellm_client_via_real_registry():
    cfg = load_config(environ={}, llm={"strategy": "litellm", "model": "azure/gpt-4.1-mini"})
    # Uses the REAL default_registry + _FALLBACK_ORDER: this pins the trap where a
    # strategy registered but absent from _FALLBACK_ORDER silently falls through to
    # borrow-host and never runs.
    client = select_llm_client(cfg)
    assert isinstance(client, LiteLLMLLMClient)
    assert client.model_name == "azure/gpt-4.1-mini"


# ── (d) litellm does NOT auto-select on the zero-config default ──────────────────


def test_litellm_not_available_on_zero_config_default():
    assert LiteLLMStrategy().is_available(load_config(environ={})) is False


def test_litellm_available_only_when_requested_by_name():
    assert (
        LiteLLMStrategy().is_available(load_config(environ={}, llm={"strategy": STRATEGY_LITELLM}))
        is True
    )
    # A fully-specified model + api_key_env still does NOT auto-select unless the
    # strategy is litellm by name (byo-key would win that fallback race anyway).
    cfg = load_config(
        environ={}, llm={"model": "azure/gpt-4.1-mini", "api_key_env": "AZURE_API_KEY"}
    )
    assert LiteLLMStrategy().is_available(cfg) is False


# ── (f) engine/client still builds with litellm requested but no key ─────────────


def test_select_builds_litellm_lazily_with_no_key_present(monkeypatch):
    monkeypatch.delenv("AZURE_API_KEY", raising=False)
    cfg = load_config(environ={}, llm={"strategy": "litellm", "model": "azure/gpt-4.1-mini"})
    # Selection/construction must not raise despite no key being present.
    client = select_llm_client(cfg)
    assert isinstance(client, LiteLLMLLMClient)


# ── real _SdkLiteLLMBackend wiring (litellm.acompletion mocked — still hermetic) ─


def test_sdk_backend_threads_into_acompletion_and_extracts_content(monkeypatch):
    import litellm

    captured: dict[str, object] = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        captured["_keys"] = set(kwargs)
        return _FakeResponse('{"name": "Bob", "summary": "dev"}')

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    backend = _SdkLiteLLMBackend()
    content = asyncio.run(
        backend.acomplete(
            [{"role": "user", "content": "hi"}],
            model="azure/gpt-4.1-mini",
            max_tokens=256,
            api_key="sk-x",
        )
    )

    assert content == '{"name": "Bob", "summary": "dev"}'
    assert captured["model"] == "azure/gpt-4.1-mini"
    assert captured["max_tokens"] == 256
    assert captured["api_key"] == "sk-x"
    assert captured["temperature"] == 0
    assert captured["messages"] == [{"role": "user", "content": "hi"}]


def test_sdk_backend_omits_api_key_kwarg_when_none(monkeypatch):
    import litellm

    seen_keys: set[str] = set()

    async def fake_acompletion(**kwargs):
        seen_keys.update(kwargs)
        return _FakeResponse('{"x": 1}')

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    asyncio.run(
        _SdkLiteLLMBackend().acomplete(
            [{"role": "user", "content": "hi"}], model="m", max_tokens=8, api_key=None
        )
    )
    # No key → do not pass api_key at all, so litellm falls back to native env resolution.
    assert "api_key" not in seen_keys


def test_sdk_backend_wraps_provider_error_actionably(monkeypatch):
    import litellm

    async def boom(**kwargs):
        raise RuntimeError("invalid api key")

    monkeypatch.setattr(litellm, "acompletion", boom)

    with pytest.raises(LiteLLMError) as excinfo:
        asyncio.run(
            _SdkLiteLLMBackend().acomplete(
                [{"role": "user", "content": "hi"}],
                model="azure/gpt-4.1-mini",
                max_tokens=8,
                api_key=None,
            )
        )
    assert "azure/gpt-4.1-mini" in str(excinfo.value)


def test_sdk_backend_empty_content_raises(monkeypatch):
    import litellm

    async def fake_acompletion(**kwargs):
        return _FakeResponse("")

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    with pytest.raises(LiteLLMError):
        asyncio.run(
            _SdkLiteLLMBackend().acomplete(
                [{"role": "user", "content": "x"}], model="m", max_tokens=8, api_key=None
            )
        )


def test_end_to_end_client_over_mocked_litellm_no_network(monkeypatch):
    """Full path: client → _SdkLiteLLMBackend → (mocked) litellm, schema on the wire."""
    import litellm

    captured: dict[str, object] = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _FakeResponse('{"name": "Bob", "summary": "dev"}')

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setenv("MEMRELAY_UT_KEY", "sk-x")

    client = LiteLLMLLMClient(model="azure/gpt-4.1-mini", api_key_env="MEMRELAY_UT_KEY")
    result = asyncio.run(
        client._generate_response(
            [Message(role="user", content="Bob is a dev")], response_model=_ExtractedNode
        )
    )

    assert result == {"name": "Bob", "summary": "dev"}
    assert "summary" in captured["messages"][-1]["content"]  # schema reached the wire
    assert captured["api_key"] == "sk-x"
