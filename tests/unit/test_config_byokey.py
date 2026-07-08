"""Regression tests for the additive byo-key config fields (E4-S4, #38).

The byo-key strategy needs a few extra knobs on ``LLMConfig`` /
``EmbeddingsConfig`` (``provider`` / ``api_key_env`` / ``model``). Those fields
were added *additively* — the key-less default path must be byte-for-byte
unchanged. These tests lock that guarantee:

* the new fields default to ``None`` (byo-key is opt-in);
* the whole key-less default config is unchanged (a frozen snapshot of the
  schema-defined sections trips if any default shifts or a new field turns
  non-``None``);
* the new keys still thread through ``_known()`` / ``_config_from_dict`` when
  explicitly provided.
"""

from __future__ import annotations

from memrelay.config import EmbeddingsConfig, LLMConfig, load_config


def test_byokey_fields_default_to_none() -> None:
    cfg = load_config(environ={})
    assert cfg.llm.provider is None
    assert cfg.llm.api_key_env is None
    assert cfg.llm.model is None
    assert cfg.embeddings.api_key_env is None


def test_default_config_sections_unchanged() -> None:
    """The key-less default llm/embeddings config is unchanged by the extension.

    Compared against explicit dataclass snapshots (dataclass ``__eq__`` covers
    every field), so adding a non-``None`` default or altering an existing one
    would fail here.
    """
    cfg = load_config(environ={})
    assert cfg.llm == LLMConfig(
        strategy="borrow-host",
        host="copilot",
        provider=None,
        api_key_env=None,
        model=None,
    )
    assert cfg.embeddings == EmbeddingsConfig(
        provider="local",
        model="BAAI/bge-small-en-v1.5",
        api_key_env=None,
    )


def test_byokey_fields_populate_when_set() -> None:
    """The new keys are honored end-to-end (not dropped by ``_known()``)."""
    cfg = load_config(
        environ={},
        llm={
            "strategy": "byo-key",
            "provider": "openai",
            "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini",
        },
        embeddings={"provider": "openai", "api_key_env": "OPENAI_API_KEY"},
    )
    assert cfg.llm.strategy == "byo-key"
    assert cfg.llm.provider == "openai"
    assert cfg.llm.api_key_env == "OPENAI_API_KEY"
    assert cfg.llm.model == "gpt-4o-mini"
    assert cfg.embeddings.api_key_env == "OPENAI_API_KEY"


def test_byokey_fields_via_env_overrides() -> None:
    """Env-var nesting reaches the new fields too (parity with existing keys)."""
    cfg = load_config(
        environ={
            "MEMRELAY_LLM__STRATEGY": "byo-key",
            "MEMRELAY_LLM__PROVIDER": "openai",
            "MEMRELAY_LLM__API_KEY_ENV": "OPENAI_API_KEY",
            "MEMRELAY_LLM__MODEL": "gpt-4o-mini",
        }
    )
    assert cfg.llm.provider == "openai"
    assert cfg.llm.api_key_env == "OPENAI_API_KEY"
    assert cfg.llm.model == "gpt-4o-mini"
