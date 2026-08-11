"""Contract tests for fail-closed shipped-product provider configuration."""

from __future__ import annotations

import pytest
from memrelay_eval.adapters.memrelay import (
    EXPECTED_OPENAI_BASE_URL,
    build_framework_process_environments,
    verify_framework_preflight,
)
from memrelay_eval.domain.errors import ConformancePauseError


def _preflight(embedding_cache_dir, **overrides: object) -> object:
    daemon, agent, mcp = build_framework_process_environments()
    values: dict[str, object] = {
        "llm_strategy": "byo-key",
        "framework_model": "gpt-4.1-mini-2025-04-14",
        "openai_base_url": EXPECTED_OPENAI_BASE_URL,
        "daemon_environment": daemon,
        "agent_environment": agent,
        "mcp_environment": mcp,
        "embedding_cache_dir": embedding_cache_dir,
    }
    values.update(overrides)
    return verify_framework_preflight(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("strategy", ("borrow-host", "litellm", "local", "azure", "nonsense"))
def test_preflight_rejects_every_nonfrozen_strategy(
    strategy: str, tmp_path, frozen_embedding_artifact
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    frozen_embedding_artifact(home)
    with pytest.raises(ConformancePauseError):
        _preflight(home / "models", llm_strategy=strategy)


def test_preflight_requires_normalized_exact_direct_openai_url(
    tmp_path, frozen_embedding_artifact
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    frozen_embedding_artifact(home)
    cache_dir = home / "models"
    evidence = _preflight(cache_dir, openai_base_url="HTTPS://API.OPENAI.COM/v1/")
    assert evidence.is_ready
    assert evidence.openai_base_url == EXPECTED_OPENAI_BASE_URL
    assert evidence.expected_openai_base_url == EXPECTED_OPENAI_BASE_URL

    with pytest.raises(ConformancePauseError):
        _preflight(cache_dir, openai_base_url="https://api.openai.com.evil.invalid/v1")
    daemon, agent, mcp = build_framework_process_environments(
        openai_base_url="https://api.openai.com.evil.invalid/v1"
    )
    with pytest.raises(ConformancePauseError):
        verify_framework_preflight(
            llm_strategy="byo-key",
            framework_model="gpt-4.1-mini-2025-04-14",
            openai_base_url=EXPECTED_OPENAI_BASE_URL,
            daemon_environment=daemon,
            agent_environment=agent,
            mcp_environment=mcp,
            embedding_cache_dir=cache_dir,
        )


@pytest.mark.parametrize(
    "variable",
    ("openai_api_key", "OpenAI_Base_Url", "ＯＰＥＮＡＩ＿ＡＰＩ＿ＫＥＹ", "OPENAI_TOKEN"),
)
def test_preflight_detects_normalized_credential_leaks(
    variable: str, tmp_path, frozen_embedding_artifact
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    frozen_embedding_artifact(home)
    daemon, agent, mcp = build_framework_process_environments()
    agent[variable] = "synthetic-canary"
    with pytest.raises(ConformancePauseError):
        verify_framework_preflight(
            llm_strategy="byo-key",
            framework_model="gpt-4.1-mini-2025-04-14",
            openai_base_url=EXPECTED_OPENAI_BASE_URL,
            daemon_environment=daemon,
            agent_environment=agent,
            mcp_environment=mcp,
            embedding_cache_dir=home / "models",
        )


@pytest.mark.parametrize(
    "variable",
    ("openai_api_key", "OpenAI_Base_Url", "ＯＰＥＮＡＩ＿ＡＰＩ＿ＫＥＹ", "OPENAI_TOKEN"),
)
def test_preflight_detects_normalized_credential_leaks_in_mcp_environment(
    variable: str, tmp_path, frozen_embedding_artifact
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    frozen_embedding_artifact(home)
    daemon, agent, mcp = build_framework_process_environments()
    mcp[variable] = "synthetic-canary"
    with pytest.raises(ConformancePauseError):
        verify_framework_preflight(
            llm_strategy="byo-key",
            framework_model="gpt-4.1-mini-2025-04-14",
            openai_base_url=EXPECTED_OPENAI_BASE_URL,
            daemon_environment=daemon,
            agent_environment=agent,
            mcp_environment=mcp,
            embedding_cache_dir=home / "models",
        )
