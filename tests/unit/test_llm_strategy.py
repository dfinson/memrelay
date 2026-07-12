"""Unit tests for the pluggable LLM strategy seam (E4-S6 / #63) and byo-key/local."""

from __future__ import annotations

import asyncio

import pytest
from graphiti_core.llm_client.client import LLMClient

from memrelay.config import load_config
from memrelay.engine.llm.borrow_host import (
    BorrowHostLLMClient,
    ClaudeHostProcess,
    CopilotHostProcess,
    HostProcessError,
)
from memrelay.engine.llm.byo_key import ByoKeyConfigError, ByoKeyLLMClient
from memrelay.engine.llm.strategy import (
    STRATEGY_BORROW_HOST,
    STRATEGY_BYO_KEY,
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
