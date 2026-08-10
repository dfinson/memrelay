"""Shipped MCP surface contracts."""

from __future__ import annotations

import pytest
from memrelay_eval.adapters.memrelay import (
    PRODUCT_SHIPPED_TOOL_NAMES,
    build_product_identity_chain,
    product_tool_contract,
    require_product_tool_visibility,
)
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.domain.states import EvaluationStratum
from memrelay_eval.orchestration.stages import require_product_stratum_aggregation


def test_product_tool_contract_is_exact() -> None:
    contract = product_tool_contract()
    assert contract.tool_names == PRODUCT_SHIPPED_TOOL_NAMES
    assert contract.permissions["memory_note"] == ("write",)
    assert require_product_tool_visibility(contract.tool_names).is_exact


def test_product_tool_contract_rejects_a_fourth_mcp_tool() -> None:
    with pytest.raises(ConformancePauseError):
        require_product_tool_visibility((*PRODUCT_SHIPPED_TOOL_NAMES, "memory_engine_write"))


def test_orchestration_aggregation_rejects_product_and_engine_records() -> None:
    with pytest.raises(ValueError, match="pooling is forbidden"):
        require_product_stratum_aggregation(
            (
                build_product_identity_chain(stratum=EvaluationStratum.PRODUCT),
                build_product_identity_chain(stratum=EvaluationStratum.DIRECT_ENGINE),
            )
        )
