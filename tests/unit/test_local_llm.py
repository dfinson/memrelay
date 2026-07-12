"""Unit tests for the local LLM strategy (E4-S7 / #64).

Fully hermetic: the network is never touched. Fast paths inject a fake
:class:`LocalBackend`; the real :class:`OllamaBackend` HTTP wiring is exercised
by monkeypatching ``urllib.request.urlopen``. Async coroutines are driven with
``asyncio.run`` (matching test_llm_strategy.py) so the suite needs no
pytest-asyncio plugin.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from memrelay.config import load_config
from memrelay.engine.llm.local import (
    DEFAULT_LOCAL_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    LocalLLMClient,
    LocalLLMError,
    OllamaBackend,
)
from memrelay.engine.llm.strategy import LocalStrategy


class _ExtractedNode(BaseModel):
    name: str
    summary: str


class _RecordingBackend:
    """Fake :class:`LocalBackend` that records calls and replays canned replies.

    Once the canned replies are exhausted the last reply is repeated, which makes
    "always returns junk" (retries-exhausted) cases trivial to express.
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, object]] = []

    async def chat(self, messages, *, model: str, max_tokens: int) -> str:
        self.calls.append({"messages": messages, "model": model, "max_tokens": max_tokens})
        index = min(len(self.calls) - 1, len(self._replies) - 1)
        return self._replies[index]


class _FakeResponse:
    """Minimal context-manager stand-in for ``urllib.request.urlopen``'s result."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _messages() -> list[Message]:
    return [
        Message(role="system", content="Extract entities."),
        Message(role="user", content="Alice is a person."),
    ]


# ── construction is cheap / no network, no key ───────────────────────────────────


def test_client_construction_is_cheap_and_offline():
    # No base_url/model/backend given: defaults apply, nothing is dialed.
    client = LocalLLMClient()
    assert client.base_url == DEFAULT_OLLAMA_BASE_URL
    assert client.model_name == DEFAULT_LOCAL_MODEL
    assert isinstance(client._backend, OllamaBackend)


def test_strategy_build_client_is_cheap_and_uses_defaults():
    cfg = load_config(environ={}, llm={"strategy": "local"})
    client = LocalStrategy().build_client(cfg)
    assert isinstance(client, LocalLLMClient)
    assert client.base_url == DEFAULT_OLLAMA_BASE_URL
    assert client.model_name == "llama3.1"


def test_strategy_build_client_honors_configured_url_and_model():
    cfg = load_config(
        environ={},
        llm={"strategy": "local", "local_base_url": "http://gpu:11434", "local_model": "mistral"},
    )
    client = LocalStrategy().build_client(cfg)
    assert client.base_url == "http://gpu:11434"
    assert client.model_name == "mistral"


# ── schema-in-prompt + JSON parsing (fake backend, no network) ───────────────────


def test_schema_is_embedded_in_prompt_and_json_reply_is_parsed():
    backend = _RecordingBackend(['{"name": "Alice", "summary": "a person"}'])
    client = LocalLLMClient(backend=backend, model="llama3.1")

    result = asyncio.run(client._generate_response(_messages(), response_model=_ExtractedNode))

    assert result == {"name": "Alice", "summary": "a person"}
    # The response_model's JSON schema must reach the model (schema-in-prompt).
    last_content = backend.calls[0]["messages"][-1]["content"]
    assert "summary" in last_content
    assert "properties" in last_content
    assert backend.calls[0]["model"] == "llama3.1"


def test_no_response_model_sends_no_schema_instruction():
    backend = _RecordingBackend(['{"ack": true}'])
    client = LocalLLMClient(backend=backend)

    result = asyncio.run(client._generate_response(_messages()))

    assert result == {"ack": True}
    assert "JSON schema" not in backend.calls[0]["messages"][-1]["content"]


def test_code_fenced_json_is_stripped_and_parsed():
    backend = _RecordingBackend(['```json\n{"name": "Bo", "summary": "dev"}\n```'])
    client = LocalLLMClient(backend=backend)

    result = asyncio.run(client._generate_response(_messages(), response_model=_ExtractedNode))

    assert result == {"name": "Bo", "summary": "dev"}


def test_json_object_recovered_from_surrounding_prose():
    backend = _RecordingBackend(['Sure! Here you go: {"name": "Cy", "summary": "qa"} — done.'])
    client = LocalLLMClient(backend=backend)

    result = asyncio.run(client._generate_response(_messages(), response_model=_ExtractedNode))

    assert result == {"name": "Cy", "summary": "qa"}


# ── bounded JSON-correction retry ────────────────────────────────────────────────


def test_retries_with_correction_then_succeeds():
    backend = _RecordingBackend(["not json at all", '{"ok": true}'])
    client = LocalLLMClient(backend=backend, max_json_retries=2)

    result = asyncio.run(client._generate_response(_messages()))

    assert result == {"ok": True}
    assert len(backend.calls) == 2
    # The retry must carry a correction instruction back to the model.
    assert "not valid JSON" in backend.calls[1]["messages"][-1]["content"]


def test_raises_local_error_after_retries_exhausted():
    backend = _RecordingBackend(["still not json"])  # repeated for every attempt
    client = LocalLLMClient(backend=backend, max_json_retries=2)

    with pytest.raises(LocalLLMError):
        asyncio.run(client._generate_response(_messages()))

    assert len(backend.calls) == 3  # initial + 2 retries


# ── real OllamaBackend HTTP wiring (mocked urlopen — still hermetic) ─────────────


def test_ollama_backend_posts_to_api_chat_and_parses_content(monkeypatch):
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(
            json.dumps({"message": {"role": "assistant", "content": '{"x": 1}'}}).encode("utf-8")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    backend = OllamaBackend("http://gpu:11434")
    content = asyncio.run(
        backend.chat([{"role": "user", "content": "hi"}], model="mistral", max_tokens=256)
    )

    assert content == '{"x": 1}'
    request = captured["request"]
    assert request.full_url == "http://gpu:11434/api/chat"
    assert request.get_method() == "POST"
    sent = json.loads(request.data)
    assert sent["model"] == "mistral"
    assert sent["stream"] is False
    assert sent["format"] == "json"
    assert sent["options"]["num_predict"] == 256
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


def test_ollama_backend_trailing_slash_is_normalized():
    assert OllamaBackend("http://host:1234/").base_url == "http://host:1234"
    assert OllamaBackend(None).base_url == DEFAULT_OLLAMA_BASE_URL


def test_ollama_backend_wraps_connection_error(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    backend = OllamaBackend("http://localhost:11434")
    with pytest.raises(LocalLLMError) as excinfo:
        asyncio.run(
            backend.chat([{"role": "user", "content": "hi"}], model="llama3.1", max_tokens=16)
        )
    assert "localhost:11434" in str(excinfo.value)


def test_ollama_backend_empty_content_raises(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse(json.dumps({"message": {"content": ""}}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(LocalLLMError):
        asyncio.run(
            OllamaBackend().chat([{"role": "user", "content": "x"}], model="m", max_tokens=8)
        )


def test_end_to_end_client_over_mocked_ollama_no_network_no_key(monkeypatch):
    """Full path: client → OllamaBackend → (mocked) HTTP, schema-in-prompt on the wire."""
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return _FakeResponse(
            json.dumps({"message": {"content": '{"name": "Bob", "summary": "dev"}'}}).encode(
                "utf-8"
            )
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = LocalLLMClient(base_url="http://localhost:11434", model="llama3.1")
    result = asyncio.run(
        client._generate_response(
            [Message(role="user", content="Bob is a dev")], response_model=_ExtractedNode
        )
    )

    assert result == {"name": "Bob", "summary": "dev"}
    sent = json.loads(captured["request"].data)
    assert "summary" in sent["messages"][-1]["content"]  # schema reached the wire


# ── LocalStrategy availability is strictly opt-in ────────────────────────────────


def test_is_available_is_false_for_zero_config_default():
    # Zero-config default is borrow-host with no local_base_url → local not selected,
    # preserving the borrow-host default and the fallback chain.
    assert LocalStrategy().is_available(load_config(environ={})) is False


def test_is_available_true_when_strategy_local():
    assert LocalStrategy().is_available(load_config(environ={}, llm={"strategy": "local"})) is True


def test_is_available_true_when_local_base_url_configured():
    cfg = load_config(environ={}, llm={"local_base_url": "http://localhost:11434"})
    assert LocalStrategy().is_available(cfg) is True


# ── additive config fields load from env with offline-friendly defaults ──────────


def test_local_config_fields_default_offline_friendly():
    cfg = load_config(environ={})
    assert cfg.llm.local_base_url is None
    assert cfg.llm.local_model == "llama3.1"


def test_local_config_fields_load_from_env():
    cfg = load_config(
        environ={
            "MEMRELAY_LLM__LOCAL_BASE_URL": "http://gpu:11434",
            "MEMRELAY_LLM__LOCAL_MODEL": "mistral",
        }
    )
    assert cfg.llm.local_base_url == "http://gpu:11434"
    assert cfg.llm.local_model == "mistral"
