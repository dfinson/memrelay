"""Unit tests for the pluggable LLM strategy seam (E4-S6 / #63) and byo-key/local."""

from __future__ import annotations

import asyncio

import pytest
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from memrelay.config import load_config
from memrelay.engine.llm.borrow_host import (
    BorrowHostLLMClient,
    ClaudeHostProcess,
    CopilotHostProcess,
    HostProcessError,
)
from memrelay.engine.llm.byo_key import ByoKeyConfigError, ByoKeyLLMClient
from memrelay.engine.llm.litellm_client import LiteLLMLLMClient
from memrelay.engine.llm.strategy import (
    STRATEGY_BORROW_HOST,
    STRATEGY_BYO_KEY,
    STRATEGY_LITELLM,
    BorrowHostStrategy,
    ByoKeyStrategy,
    LLMStrategy,
    select_llm_client,
)


class _Sentinel(LLMClient):
    def __init__(self, tag: str) -> None:
        super().__init__(None, cache=False)
        self.tag = tag

    async def _generate_response(self, *args, **kwargs):  # pragma: no cover - never called
        return {}


class _FixedStrategy(LLMStrategy):
    def __init__(self, name: str, available: bool, tag: str) -> None:
        self.name = name
        self._available = available
        self._tag = tag

    def is_available(self, cfg) -> bool:
        return self._available

    def build_client(self, cfg) -> LLMClient:
        return _Sentinel(self._tag)


def _registry(borrow: bool, byo: bool, local: bool) -> dict[str, LLMStrategy]:
    return {
        "borrow-host": _FixedStrategy("borrow-host", borrow, "borrow"),
        "byo-key": _FixedStrategy("byo-key", byo, "byo"),
        "local": _FixedStrategy("local", local, "local"),
    }


def test_selects_requested_when_available():
    cfg = load_config(environ={}, llm={"strategy": STRATEGY_BYO_KEY})
    client = select_llm_client(cfg, registry=_registry(borrow=True, byo=True, local=False))
    assert client.tag == "byo"


def test_falls_back_to_next_available():
    cfg = load_config(environ={}, llm={"strategy": STRATEGY_BYO_KEY})
    # byo-key requested but unavailable → chain tries borrow-host next.
    client = select_llm_client(cfg, registry=_registry(borrow=True, byo=False, local=False))
    assert client.tag == "borrow"


def test_builds_requested_lazily_when_none_available():
    cfg = load_config(environ={}, llm={"strategy": STRATEGY_BYO_KEY})
    client = select_llm_client(cfg, registry=_registry(borrow=False, byo=False, local=False))
    assert client.tag == "byo"


def test_requesting_litellm_builds_litellm_client_via_real_registry():
    # Pins the _FALLBACK_ORDER trap: a strategy registered in default_registry() but
    # missing from _FALLBACK_ORDER would silently fall through to borrow-host when
    # requested. Uses the REAL registry + chain (no injected registry) on purpose.
    cfg = load_config(environ={}, llm={"strategy": STRATEGY_LITELLM, "model": "azure/gpt-4.1-mini"})
    client = select_llm_client(cfg)
    assert isinstance(client, LiteLLMLLMClient)


def test_byokey_strategy_availability_follows_env(monkeypatch):
    cfg = load_config(
        environ={},
        llm={"strategy": "byo-key", "provider": "openai", "api_key_env": "MEMRELAY_UT_KEY"},
    )
    monkeypatch.delenv("MEMRELAY_UT_KEY", raising=False)
    assert ByoKeyStrategy().is_available(cfg) is False
    monkeypatch.setenv("MEMRELAY_UT_KEY", "sk-not-real")
    assert ByoKeyStrategy().is_available(cfg) is True


def test_byokey_client_is_lazy_and_needs_key(monkeypatch):
    monkeypatch.delenv("MEMRELAY_UT_KEY", raising=False)
    cfg = load_config(
        environ={},
        llm={
            "strategy": "byo-key",
            "provider": "openai",
            "api_key_env": "MEMRELAY_UT_KEY",
            "model": "gpt-4o-mini",
        },
    )
    # Construction must not read the key or touch the network.
    client = ByoKeyLLMClient(cfg)
    # Building the delegate without a key raises a clear config error (no network).
    with pytest.raises(ByoKeyConfigError):
        client._build_delegate()


class _StructuredResponse(BaseModel):
    entities: list[str]


def test_byokey_uses_delegate_public_contract(monkeypatch):
    cfg = load_config(
        environ={},
        llm={
            "strategy": "byo-key",
            "provider": "openai",
            "api_key_env": "MEMRELAY_UT_KEY",
            "model": "gpt-4o-mini",
        },
    )
    client = ByoKeyLLMClient(cfg)
    calls: list[dict] = []
    token_tracker = object()

    class _Delegate:
        def __init__(self) -> None:
            self.token_tracker = token_tracker

        async def generate_response(self, messages, **kwargs):
            calls.append({"messages": messages, **kwargs})
            response, input_tokens, output_tokens = await self._generate_response()
            assert (input_tokens, output_tokens) == (12, 4)
            return response

        async def _generate_response(self, *args, **kwargs):
            return {"entities": ["Ada"]}, 12, 4

    client._delegate = _Delegate()
    messages = [Message(role="user", content="Extract Ada")]
    response = asyncio.run(
        client.generate_response(
            messages,
            response_model=_StructuredResponse,
            max_tokens=512,
            group_id="owner/repo",
            prompt_name="extract_nodes",
            attribute_extraction=True,
        )
    )

    assert response == {"entities": ["Ada"]}
    assert len(calls) == 1
    assert calls[0]["messages"] == messages
    assert calls[0]["response_model"] is _StructuredResponse
    assert calls[0]["max_tokens"] == 512
    assert calls[0]["group_id"] == "owner/repo"
    assert calls[0]["prompt_name"] == "extract_nodes"
    assert calls[0]["attribute_extraction"] is True
    assert client.token_tracker is token_tracker


def test_valid_byokey_selection_never_falls_back_to_borrow_host(monkeypatch):
    monkeypatch.setenv("MEMRELAY_UT_KEY", "sk-not-real")
    cfg = load_config(
        environ={},
        llm={
            "strategy": "byo-key",
            "provider": "openai",
            "api_key_env": "MEMRELAY_UT_KEY",
            "model": "gpt-4o-mini",
        },
    )

    def fail_if_probed(self, cfg):
        raise AssertionError("borrow-host must not be probed after valid byo-key selection")

    monkeypatch.setattr(BorrowHostStrategy, "is_available", fail_if_probed)
    assert isinstance(select_llm_client(cfg), ByoKeyLLMClient)


# ── BorrowHostStrategy host→process registry (E4 / #87) ──────────────────────────


def test_borrow_host_builds_claude_process_for_claude_host():
    cfg = load_config(environ={}, llm={"strategy": STRATEGY_BORROW_HOST, "host": "claude"})
    client = BorrowHostStrategy().build_client(cfg)
    assert isinstance(client, BorrowHostLLMClient)
    assert isinstance(client._host, ClaudeHostProcess)


def test_borrow_host_builds_copilot_process_for_copilot_and_default():
    cfg_copilot = load_config(environ={}, llm={"strategy": STRATEGY_BORROW_HOST, "host": "copilot"})
    assert isinstance(BorrowHostStrategy().build_client(cfg_copilot)._host, CopilotHostProcess)
    # Default (host omitted → config default "copilot") must not regress.
    cfg_default = load_config(environ={}, llm={"strategy": STRATEGY_BORROW_HOST})
    assert isinstance(BorrowHostStrategy().build_client(cfg_default)._host, CopilotHostProcess)


def test_borrow_host_unknown_host_is_unavailable_but_builds_and_fails_loud():
    cfg = load_config(environ={}, llm={"strategy": STRATEGY_BORROW_HOST, "host": "gemini"})
    strategy = BorrowHostStrategy()
    # Unknown agent-id → unavailable, so the fallback chain moves on.
    assert strategy.is_available(cfg) is False
    # Construction must NOT raise — engine construction never crashes on a bad host...
    client = strategy.build_client(cfg)
    assert isinstance(client, BorrowHostLLMClient)
    # ...the loud, clear failure surfaces only at extraction/call time.
    with pytest.raises(HostProcessError) as excinfo:
        asyncio.run(client._host.complete("anything"))
    assert "gemini" in str(excinfo.value)
