"""Unit coverage for the shipped product-stratum controls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from memrelay_eval.adapters.memrelay import (
    PRODUCT_SHIPPED_TOOL_NAMES,
    MemrelayProductTreatment,
    build_framework_process_environments,
    build_product_identity_chain,
    product_tool_contract,
    require_product_tool_visibility,
    verify_framework_preflight,
)
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.domain.policies import require_same_product_identity_chain
from memrelay_eval.domain.states import EvaluationStratum


def test_product_tool_contract_is_exact() -> None:
    contract = product_tool_contract()
    assert contract.tool_names == PRODUCT_SHIPPED_TOOL_NAMES
    assert contract.permissions["memory_note"] == ("write",)
    assert require_product_tool_visibility(contract.tool_names).is_exact


def test_framework_preflight_requires_daemon_only_openai_boundary() -> None:
    daemon_env, agent_env, mcp_env = build_framework_process_environments()
    evidence = verify_framework_preflight(
        llm_strategy="byo-key",
        framework_model="gpt-4.1-mini-2025-04-14",
        openai_base_url="https://api.openai.com/v1",
        daemon_environment=daemon_env,
        agent_environment=agent_env,
        mcp_environment=mcp_env,
        client_name="OpenAIClient",
    )

    assert evidence.is_ready
    assert evidence.identity_chain.stratum is EvaluationStratum.PRODUCT
    assert evidence.tool_contract.tool_names == PRODUCT_SHIPPED_TOOL_NAMES


def test_framework_preflight_rejects_agent_leak() -> None:
    daemon_env, agent_env, mcp_env = build_framework_process_environments()
    agent_env = {**agent_env, "OPENAI_API_KEY": "leak"}

    with pytest.raises(ConformancePauseError):
        verify_framework_preflight(
            llm_strategy="byo-key",
            framework_model="gpt-4.1-mini-2025-04-14",
            openai_base_url="https://api.openai.com/v1",
            daemon_environment=daemon_env,
            agent_environment=agent_env,
            mcp_environment=mcp_env,
            client_name="OpenAIClient",
        )


def test_product_identity_chain_is_stratum_scoped() -> None:
    chain = build_product_identity_chain(stratum=EvaluationStratum.PRODUCT)
    assert chain.stratum is EvaluationStratum.PRODUCT
    assert chain.to_record()["stratum"] == "product"
    assert require_same_product_identity_chain((chain,)) == chain


def test_tool_call_evidence_classifies_zero_result() -> None:
    treatment = MemrelayProductTreatment()
    evidence = treatment._call_evidence(  # noqa: SLF001 - classifier is part of the contract
        "memory_recall",
        {"query": "empty"},
        SimpleNamespace(isError=False, content=[SimpleNamespace(text="")]),
    )

    assert evidence.result_kind == "zero_result"
    assert evidence.is_error is False
