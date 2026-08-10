"""Direct-engine records and product/upper-bound stratum policy."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from memrelay_eval.canonical import attach_digest, canonical_digest

from .errors import DirectEngineBoundaryError, StratumPoolingError
from .ids import (
    AnalysisId,
    AssignmentId,
    ClaimId,
    CostEntryId,
    EndpointId,
    ProtocolId,
    ReportId,
    RunId,
    RuntimeId,
)
from .states import EvaluationStratum

ENGINE_CLAIM_KIND = "mechanism_upper_bound"
ENGINE_CLAIM_LABEL = "engine upper bound"
FRAMEWORK_LLM_STRATEGY = "byo-key"
FRAMEWORK_LLM_PROVIDER = "openai"
FRAMEWORK_LLM_MODEL = "gpt-4.1-mini-2025-04-14"
FRAMEWORK_EMBEDDINGS_PROVIDER = "local"
FRAMEWORK_EMBEDDINGS_MODEL = "BAAI/bge-small-en-v1.5"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class StratifiedOperation(StrEnum):
    EXPLICIT = "explicit_stratified"


class DirectEngineExecutionMode(StrEnum):
    UNPAID_FAKE = "unpaid_fake"
    LIVE_CONFORMANCE = "live_conformance"


@dataclass(frozen=True, slots=True)
class LiveEngineEnvelope:
    """Frozen positive limits required before any paid framework access."""

    maximum_calls: int
    maximum_tokens: int
    maximum_usd: float
    maximum_wall_seconds: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.maximum_calls, int)
            or isinstance(self.maximum_calls, bool)
            or self.maximum_calls <= 0
            or not isinstance(self.maximum_tokens, int)
            or isinstance(self.maximum_tokens, bool)
            or self.maximum_tokens <= 0
            or not isinstance(self.maximum_usd, (int, float))
            or isinstance(self.maximum_usd, bool)
            or not math.isfinite(self.maximum_usd)
            or self.maximum_usd <= 0
            or not isinstance(self.maximum_wall_seconds, (int, float))
            or isinstance(self.maximum_wall_seconds, bool)
            or not math.isfinite(self.maximum_wall_seconds)
            or self.maximum_wall_seconds <= 0
        ):
            raise DirectEngineBoundaryError("live_engine_envelope_invalid")

    def to_document(self) -> dict[str, object]:
        return {
            "maximum_calls": self.maximum_calls,
            "maximum_tokens": self.maximum_tokens,
            "maximum_usd": self.maximum_usd,
            "maximum_wall_seconds": self.maximum_wall_seconds,
        }


@dataclass(frozen=True, slots=True)
class FrameworkConfiguration:
    """Non-secret framework settings shared by product and engine strata."""

    llm_strategy: str = FRAMEWORK_LLM_STRATEGY
    llm_provider: str = FRAMEWORK_LLM_PROVIDER
    llm_model: str = FRAMEWORK_LLM_MODEL
    llm_api_key_env: str = "OPENAI_API_KEY"
    embeddings_provider: str = FRAMEWORK_EMBEDDINGS_PROVIDER
    embeddings_model: str = FRAMEWORK_EMBEDDINGS_MODEL

    def __post_init__(self) -> None:
        if self.to_document() != frozen_framework_configuration().to_document():
            raise DirectEngineBoundaryError("framework_configuration_drift")

    def to_document(self) -> dict[str, str]:
        return {
            "llm_strategy": self.llm_strategy,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_api_key_env": self.llm_api_key_env,
            "embeddings_provider": self.embeddings_provider,
            "embeddings_model": self.embeddings_model,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())


def frozen_framework_configuration() -> FrameworkConfiguration:
    """Build the one registered framework route without reading a credential."""

    instance = object.__new__(FrameworkConfiguration)
    object.__setattr__(instance, "llm_strategy", FRAMEWORK_LLM_STRATEGY)
    object.__setattr__(instance, "llm_provider", FRAMEWORK_LLM_PROVIDER)
    object.__setattr__(instance, "llm_model", FRAMEWORK_LLM_MODEL)
    object.__setattr__(instance, "llm_api_key_env", "OPENAI_API_KEY")
    object.__setattr__(instance, "embeddings_provider", FRAMEWORK_EMBEDDINGS_PROVIDER)
    object.__setattr__(instance, "embeddings_model", FRAMEWORK_EMBEDDINGS_MODEL)
    return instance


def require_framework_configuration_parity(
    product: FrameworkConfiguration, engine: FrameworkConfiguration
) -> str:
    """Return the shared digest or fail before either treatment is exposed."""

    if product.digest != engine.digest:
        raise DirectEngineBoundaryError("framework_configuration_parity_mismatch")
    return product.digest


@dataclass(frozen=True, slots=True)
class RenderingContract:
    """Frozen retrieval-to-agent rendering behavior for the upper-bound stratum."""

    schema_version: str
    search_template: str
    empty_search_text: str

    def __post_init__(self) -> None:
        if not self.schema_version or not self.search_template or not self.empty_search_text:
            raise DirectEngineBoundaryError("rendering_contract_invalid")
        if "{name}" not in self.search_template or "{summary}" not in self.search_template:
            raise DirectEngineBoundaryError("rendering_contract_invalid")

    def to_document(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "search_template": self.search_template,
            "empty_search_text": self.empty_search_text,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())


@dataclass(frozen=True, slots=True)
class StratumAuthority:
    """All identities that must remain distinct between causal strata."""

    stratum: EvaluationStratum
    protocol_id: ProtocolId
    assignment_id: AssignmentId
    run_id: RunId
    runtime_id: RuntimeId
    endpoint_id: EndpointId
    cost_entry_id: CostEntryId
    analysis_id: AnalysisId
    report_id: ReportId
    claim_id: ClaimId
    claim_kind: str
    claim_label: str

    def __post_init__(self) -> None:
        if self.stratum is EvaluationStratum.DIRECT_ENGINE:
            if self.claim_kind != ENGINE_CLAIM_KIND or self.claim_label != ENGINE_CLAIM_LABEL:
                raise DirectEngineBoundaryError("engine_claim_language_invalid")
        elif self.claim_kind == ENGINE_CLAIM_KIND or self.claim_label == ENGINE_CLAIM_LABEL:
            raise DirectEngineBoundaryError("product_claim_uses_engine_language")

    def to_document(self) -> dict[str, str]:
        return {
            "stratum": self.stratum.value,
            "protocol_id": str(self.protocol_id),
            "assignment_id": str(self.assignment_id),
            "run_id": str(self.run_id),
            "runtime_id": str(self.runtime_id),
            "endpoint_id": str(self.endpoint_id),
            "cost_entry_id": str(self.cost_entry_id),
            "analysis_id": str(self.analysis_id),
            "report_id": str(self.report_id),
            "claim_id": str(self.claim_id),
            "claim_kind": self.claim_kind,
            "claim_label": self.claim_label,
        }


def require_distinct_stratum_authorities(
    product: StratumAuthority, engine: StratumAuthority
) -> None:
    """Reject any identity reused across product and direct-engine protocols."""

    if product.stratum is not EvaluationStratum.PRODUCT:
        raise DirectEngineBoundaryError("product_stratum_authority_invalid")
    if engine.stratum is not EvaluationStratum.DIRECT_ENGINE:
        raise DirectEngineBoundaryError("engine_stratum_authority_invalid")
    fields = (
        "protocol_id",
        "assignment_id",
        "run_id",
        "runtime_id",
        "endpoint_id",
        "cost_entry_id",
        "analysis_id",
        "report_id",
        "claim_id",
    )
    if any(getattr(product, field) == getattr(engine, field) for field in fields):
        raise DirectEngineBoundaryError("cross_stratum_identity_reuse")


def require_stratified_operation(
    authorities: Sequence[StratumAuthority],
    operation: StratifiedOperation | None = None,
) -> tuple[EvaluationStratum, ...]:
    """Deny joins, exports, estimators, and reports that silently pool strata."""

    strata = tuple(dict.fromkeys(authority.stratum for authority in authorities))
    if len(strata) > 1 and operation is not StratifiedOperation.EXPLICIT:
        raise StratumPoolingError(StratumPoolingError.code)
    return strata


@dataclass(frozen=True, slots=True)
class EngineExternalRecord:
    """Immutable domain projection of one external plain dictionary."""

    kind: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.kind or not isinstance(self.payload, Mapping):
            raise DirectEngineBoundaryError("engine_external_record_invalid")
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))

    @classmethod
    def from_external(cls, kind: str, value: object) -> EngineExternalRecord:
        if not isinstance(value, dict):
            raise DirectEngineBoundaryError("engine_external_result_not_plain_dictionary")
        return cls(kind, value)

    def to_document(self) -> dict[str, object]:
        return {"kind": self.kind, "payload": _thaw(self.payload)}


@dataclass(frozen=True, slots=True)
class DirectEngineIsolation:
    """Value-only isolated roots delivered to one disposable framework worker."""

    worker_id: RuntimeId
    home_path: Path
    graph_path: Path
    product_graph_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        home = self.home_path.resolve()
        graph = self.graph_path.resolve()
        product = tuple(path.resolve() for path in self.product_graph_paths)
        if graph in product:
            raise DirectEngineBoundaryError("product_graph_access_denied")
        if graph.parent != home and home not in graph.parents:
            raise DirectEngineBoundaryError("engine_graph_outside_isolated_home")
        object.__setattr__(self, "home_path", home)
        object.__setattr__(self, "graph_path", graph)
        object.__setattr__(self, "product_graph_paths", product)

    @property
    def graph_path_digest(self) -> str:
        return canonical_digest({"graph_path": str(self.graph_path)})


def authority_document(authority: StratumAuthority) -> dict[str, object]:
    return attach_digest(
        {
            "artifact_type": "stratum_authority",
            "schema_version": "1.0.0",
            **authority.to_document(),
        }
    )


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    frozen: dict[str, object] = {}
    for key, nested in value.items():
        if not isinstance(key, str):
            raise DirectEngineBoundaryError("engine_external_record_invalid")
        frozen[key] = _freeze(nested)
    return MappingProxyType(frozen)


def _freeze(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    raise DirectEngineBoundaryError("engine_external_record_invalid")


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw(nested) for nested in value]
    return value
