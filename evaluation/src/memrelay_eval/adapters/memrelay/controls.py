"""Shipped product-stratum control evidence and framework preflight."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from memrelay_eval.domain.entities import ProductIdentityChain
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.domain.ids import (
    AnalysisId,
    AssignmentId,
    ClaimId,
    CostEntryId,
    EndpointId,
    ProtocolId,
    ReportId,
    StratumId,
)
from memrelay_eval.domain.policies import (
    require_same_evaluation_stratum,
    require_same_product_identity_chain,
)
from memrelay_eval.domain.states import EvaluationStratum

PRODUCT_SHIPPED_TOOL_NAMES = ("memory_detail", "memory_note", "memory_recall")
EXPECTED_FRAMEWORK_MODEL = "gpt-4.1-mini-2025-04-14"
EXPECTED_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


@dataclass(frozen=True, slots=True)
class ProductToolContract:
    """The exact shipped MCP tool surface and its ordinary controls."""

    tool_names: tuple[str, ...]
    schemas: Mapping[str, object]
    permissions: Mapping[str, object]
    budgets: Mapping[str, object]
    accounting: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_names", tuple(self.tool_names))
        object.__setattr__(self, "schemas", MappingProxyType(dict(self.schemas)))
        object.__setattr__(self, "permissions", MappingProxyType(dict(self.permissions)))
        object.__setattr__(self, "budgets", MappingProxyType(dict(self.budgets)))
        object.__setattr__(self, "accounting", MappingProxyType(dict(self.accounting)))

    def to_document(self) -> dict[str, object]:
        return {
            "tool_names": list(self.tool_names),
            "schemas": dict(self.schemas),
            "permissions": dict(self.permissions),
            "budgets": dict(self.budgets),
            "accounting": dict(self.accounting),
        }


@dataclass(frozen=True, slots=True)
class ProductToolVisibilityEvidence:
    """A value-free projection of the observed shipped MCP tool surface."""

    expected_tool_names: tuple[str, ...]
    observed_tool_names: tuple[str, ...]
    missing_tool_names: tuple[str, ...]
    unexpected_tool_names: tuple[str, ...]

    @property
    def is_exact(self) -> bool:
        return not self.missing_tool_names and not self.unexpected_tool_names

    def to_document(self) -> dict[str, object]:
        return {
            "expected_tool_names": list(self.expected_tool_names),
            "observed_tool_names": list(self.observed_tool_names),
            "missing_tool_names": list(self.missing_tool_names),
            "unexpected_tool_names": list(self.unexpected_tool_names),
            "exact": self.is_exact,
        }


@dataclass(frozen=True, slots=True)
class FrameworkPreflightEvidence:
    """Fail-closed product framework preflight evidence."""

    llm_strategy: str
    framework_model: str
    openai_base_url: str
    daemon_key_env: str
    daemon_key_only: bool
    embedding_provider: str
    embedding_model: str
    embedding_digest: str | None
    client_name: str
    rejected_fallbacks: tuple[str, ...]
    daemon_environment_keys: tuple[str, ...]
    agent_environment_keys: tuple[str, ...]
    mcp_environment_keys: tuple[str, ...]
    tool_contract: ProductToolContract
    identity_chain: ProductIdentityChain

    def __post_init__(self) -> None:
        object.__setattr__(self, "rejected_fallbacks", tuple(self.rejected_fallbacks))
        object.__setattr__(self, "daemon_environment_keys", tuple(self.daemon_environment_keys))
        object.__setattr__(self, "agent_environment_keys", tuple(self.agent_environment_keys))
        object.__setattr__(self, "mcp_environment_keys", tuple(self.mcp_environment_keys))

    @property
    def is_ready(self) -> bool:
        return (
            self.llm_strategy == "byo-key"
            and self.framework_model == EXPECTED_FRAMEWORK_MODEL
            and self.embedding_provider == "local"
            and self.embedding_model == EXPECTED_EMBEDDING_MODEL
            and self.daemon_key_only
            and not self.rejected_fallbacks
            and self.tool_contract.tool_names == PRODUCT_SHIPPED_TOOL_NAMES
            and self.identity_chain.stratum
            in {EvaluationStratum.PRODUCT, EvaluationStratum.DIRECT_ENGINE}
        )

    def to_document(self) -> dict[str, object]:
        return {
            "llm_strategy": self.llm_strategy,
            "framework_model": self.framework_model,
            "openai_base_url": self.openai_base_url,
            "daemon_key_env": self.daemon_key_env,
            "daemon_key_only": self.daemon_key_only,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_digest": self.embedding_digest,
            "client_name": self.client_name,
            "rejected_fallbacks": list(self.rejected_fallbacks),
            "daemon_environment_keys": list(self.daemon_environment_keys),
            "agent_environment_keys": list(self.agent_environment_keys),
            "mcp_environment_keys": list(self.mcp_environment_keys),
            "tool_contract": self.tool_contract.to_document(),
            "identity_chain": self.identity_chain.to_record(),
            "ready": self.is_ready,
        }


@dataclass(frozen=True, slots=True)
class ProductIdentityEnvelope:
    """A separate product or engine identity chain for one attempt."""

    chain: ProductIdentityChain
    protocol_role: str


def build_product_identity_chain(
    *,
    stratum: EvaluationStratum,
    stratum_id: StratumId | None = None,
    protocol_id: ProtocolId | None = None,
    assignment_id: AssignmentId | None = None,
    endpoint_id: EndpointId | None = None,
    cost_entry_id: CostEntryId | None = None,
    analysis_id: AnalysisId | None = None,
    report_id: ReportId | None = None,
    claim_id: ClaimId | None = None,
) -> ProductIdentityChain:
    """Create a full opaque identity chain for one product or engine stratum."""

    return ProductIdentityChain(
        stratum=stratum,
        stratum_id=stratum_id or StratumId.new(),
        protocol_id=protocol_id or ProtocolId.new(),
        assignment_id=assignment_id or AssignmentId.new(),
        endpoint_id=endpoint_id or EndpointId.new(),
        cost_entry_id=cost_entry_id or CostEntryId.new(),
        analysis_id=analysis_id or AnalysisId.new(),
        report_id=report_id or ReportId.new(),
        claim_id=claim_id or ClaimId.new(),
    )


def product_tool_contract() -> ProductToolContract:
    """Return the exact shipped MCP control surface."""

    return ProductToolContract(
        PRODUCT_SHIPPED_TOOL_NAMES,
        schemas={
            "memory_recall": {
                "input": {"query": "str", "prefer_repo": "str | null"},
                "output": "formatted map",
            },
            "memory_detail": {
                "input": {"node_uuid": "str"},
                "output": "formatted detail",
            },
            "memory_note": {
                "input": {"content": "str"},
                "output": "plain acknowledgement",
            },
        },
        permissions={
            "memory_recall": ("read",),
            "memory_detail": ("read",),
            "memory_note": ("write",),
        },
        budgets={
            "memory_note_max_bytes": 512 * 1024,
        },
        accounting={
            "tool_call": True,
            "daemon_health": True,
            "transport": True,
        },
    )


def require_product_tool_visibility(
    observed_tool_names: Sequence[str],
    *,
    expected_tool_names: Sequence[str] = PRODUCT_SHIPPED_TOOL_NAMES,
) -> ProductToolVisibilityEvidence:
    """Reject any extra or missing tool visibility at the MCP boundary."""

    observed = tuple(observed_tool_names)
    expected = tuple(expected_tool_names)
    missing = tuple(name for name in expected if name not in observed)
    unexpected = tuple(name for name in observed if name not in expected)
    evidence = ProductToolVisibilityEvidence(expected, observed, missing, unexpected)
    if not evidence.is_exact:
        raise ConformancePauseError(
            "product_tool_visibility_drift",
            "shipped MCP tool visibility is not exact",
        )
    return evidence


def verify_framework_preflight(
    *,
    llm_strategy: str,
    framework_model: str,
    openai_base_url: str,
    daemon_environment: Mapping[str, str],
    agent_environment: Mapping[str, str],
    mcp_environment: Mapping[str, str],
    daemon_key_env: str = "OPENAI_API_KEY",
    embedding_provider: str = "local",
    embedding_model: str = EXPECTED_EMBEDDING_MODEL,
    embedding_digest: str | None = None,
    client_name: str = "OpenAIClient",
    tool_contract: ProductToolContract | None = None,
    stratum: EvaluationStratum = EvaluationStratum.PRODUCT,
) -> FrameworkPreflightEvidence:
    """Fail closed on borrow-host, LiteLLM, local fallbacks, or credential leaks."""

    tool_contract = tool_contract or product_tool_contract()
    identity_chain = build_product_identity_chain(stratum=stratum)
    rejected: list[str] = []
    if llm_strategy != "byo-key":
        rejected.append(llm_strategy)
    for fallback in ("borrow-host", "litellm", "local"):
        if fallback == llm_strategy:
            rejected.append(fallback)
    if framework_model != EXPECTED_FRAMEWORK_MODEL:
        raise ConformancePauseError(
            "framework_model_drift",
            "framework model does not match the frozen product configuration",
        )
    if embedding_provider != "local" or embedding_model != EXPECTED_EMBEDDING_MODEL:
        raise ConformancePauseError(
            "embedding_configuration_drift",
            "local embeddings are not pinned to the frozen model",
        )
    daemon_key_only = (
        daemon_key_env in daemon_environment
        and daemon_key_env not in agent_environment
        and daemon_key_env not in mcp_environment
        and "OPENAI_BASE_URL" in daemon_environment
        and "OPENAI_BASE_URL" not in agent_environment
        and "OPENAI_BASE_URL" not in mcp_environment
    )
    if not daemon_key_only:
        raise ConformancePauseError(
            "daemon_key_boundary_drift",
            "OpenAI credentials are not isolated to the daemon process",
        )
    for forbidden in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        if forbidden in agent_environment or forbidden in mcp_environment:
            raise ConformancePauseError(
                "framework_credential_leak",
                "OpenAI credentials leaked to a non-daemon process",
            )
    if "borrow-host" in rejected or "litellm" in rejected or "local" in rejected:
        raise ConformancePauseError(
            "framework_route_fallback",
            "framework route fell back to a forbidden strategy",
        )
    if not client_name or "openai" not in client_name.casefold():
        raise ConformancePauseError(
            "framework_client_drift",
            "framework client is not the expected OpenAI-backed client",
        )
    if openai_base_url not in daemon_environment.get("OPENAI_BASE_URL", ""):
        raise ConformancePauseError(
            "framework_base_url_drift",
            "framework base URL is not pinned in the daemon environment",
        )
    require_same_evaluation_stratum((stratum,))
    require_same_product_identity_chain((identity_chain,))
    return FrameworkPreflightEvidence(
        llm_strategy,
        framework_model,
        openai_base_url,
        daemon_key_env,
        daemon_key_only,
        embedding_provider,
        embedding_model,
        embedding_digest,
        client_name,
        tuple(dict.fromkeys(rejected)),
        tuple(sorted(daemon_environment)),
        tuple(sorted(agent_environment)),
        tuple(sorted(mcp_environment)),
        tool_contract,
        identity_chain,
    )


def build_framework_process_environments(
    *,
    openai_api_key: str = "sk-fake-openai-key",
    openai_base_url: str = "https://api.openai.com/v1",
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return a deterministic daemon-only OpenAI boundary for unpaid conformance tests."""

    daemon_environment = {
        "OPENAI_API_KEY": openai_api_key,
        "OPENAI_BASE_URL": openai_base_url,
    }
    agent_environment: dict[str, str] = {}
    mcp_environment: dict[str, str] = {}
    return daemon_environment, agent_environment, mcp_environment
