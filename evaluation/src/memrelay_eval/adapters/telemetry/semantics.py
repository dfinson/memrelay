"""Frozen, value-safe telemetry semantics owned by the evaluator."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from memrelay_eval.canonical import CanonicalizationError, canonical_bytes
from memrelay_eval.domain.errors import TelemetryConformanceError
from memrelay_eval.domain.states import EvaluationStratum, ExposureClassification, HistoryMode
from memrelay_eval.evidence.secret_scan import scan_secret_boundaries

TELEMETRY_SCHEMA_VERSION = "1.0.0"
GENAI_MAP_VERSION = "memrelay.eval.genai-map/1.0.0"
_SAFE_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_IDENTITY_PREFIXES = {
    "experiment_id": "exp",
    "protocol_id": "protocol",
    "run_id": "run",
    "attempt_id": "attempt",
    "scenario_id": "scenario",
}
_SAFE_ATTRIBUTE_NAMES = frozenset(
    {
        "duration_ms",
        "bytes",
        "retry_number",
        "input_tokens",
        "output_tokens",
        "status_code",
        "transport_status",
        "artifact_sha256",
        "expected_count",
        "actual_count",
    }
)
_GENAI_FIELDS = {
    "gen_ai.operation.name": "memrelay.eval.genai.operation",
    "gen_ai.request.model": "memrelay.eval.genai.request_model",
    "gen_ai.response.model": "memrelay.eval.genai.response_model",
    "gen_ai.usage.input_tokens": "memrelay.eval.genai.input_tokens",
    "gen_ai.usage.output_tokens": "memrelay.eval.genai.output_tokens",
}


class SpanClass(StrEnum):
    CONTROL_ASSIGNMENT = "control.assignment"
    PROVISIONING = "provisioning"
    CLEANUP = "cleanup"
    COPILOT_SESSION = "copilot.session"
    COPILOT_MODEL_REQUEST = "copilot.model_request"
    MCP_TOOL_REQUEST = "mcp.tool_request"
    DAEMON_DISPATCH = "daemon.dispatch"
    MEMORY_WRITE = "memory.write"
    MEMORY_RETRIEVAL = "memory.retrieval"
    FRAMEWORK_EXTRACTION = "framework.extraction"
    FRAMEWORK_EMBEDDING = "framework.embedding"
    GRADER_EXECUTION = "grader.execution"
    JUDGE_ADJUDICATION = "judge.adjudication"
    ARTIFACT_PERSISTENCE = "artifact.persistence"
    INSPECT_EXPORT = "inspect.export"
    COST_RECONCILIATION = "cost.reconciliation"
    EVIDENCE_RECONCILIATION = "evidence.reconciliation"


REQUIRED_SPAN_CLASSES = frozenset(SpanClass)


def _require_code(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not _SAFE_CODE.fullmatch(value):
        raise TelemetryConformanceError("invalid_telemetry_field", (field,))
    if scan_secret_boundaries({"agent_visible_telemetry": value}):
        raise TelemetryConformanceError("secret_or_treatment_in_telemetry", (field,))
    return value


def _require_opaque_id(value: str, field: str) -> str:
    prefix = _IDENTITY_PREFIXES[field]
    if not isinstance(value, str) or not re.fullmatch(rf"{prefix}_[a-f0-9]{{32}}", value):
        raise TelemetryConformanceError("invalid_telemetry_identity", (field,))
    return value


def _require_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TelemetryConformanceError("invalid_telemetry_timestamp", (field,))
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TelemetryContext:
    """Required versioned evaluator identifiers and value-safe classifications."""

    experiment_id: str
    protocol_id: str
    run_id: str
    attempt_id: str
    scenario_id: str
    stratum_id: str
    history_mode: str
    provider: str
    credential_domain: str
    cost_source: str
    evidence_class: str
    exposure_state: str
    failure_code: str = "none"
    environment_fingerprint_sha256: str = "0" * 64
    schema_version: str = TELEMETRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TELEMETRY_SCHEMA_VERSION:
            raise TelemetryConformanceError("unknown_telemetry_schema_version")
        for field_name in ("experiment_id", "protocol_id", "run_id", "attempt_id", "scenario_id"):
            _require_opaque_id(getattr(self, field_name), field_name)
        if self.stratum_id not in {item.value for item in EvaluationStratum}:
            raise TelemetryConformanceError("invalid_telemetry_field", ("stratum_id",))
        if self.history_mode not in {item.value for item in HistoryMode}:
            raise TelemetryConformanceError("invalid_telemetry_field", ("history_mode",))
        if self.exposure_state not in {item.value for item in ExposureClassification}:
            raise TelemetryConformanceError("invalid_telemetry_field", ("exposure_state",))
        for field_name in (
            "provider",
            "credential_domain",
            "cost_source",
            "evidence_class",
            "failure_code",
        ):
            _require_code(getattr(self, field_name), field_name)
        if not isinstance(self.environment_fingerprint_sha256, str) or not _SHA256.fullmatch(
            self.environment_fingerprint_sha256
        ):
            raise TelemetryConformanceError(
                "invalid_telemetry_environment_fingerprint",
                ("environment_fingerprint_sha256",),
            )

    def attributes(self) -> dict[str, str]:
        return {
            "memrelay.eval.schema_version": self.schema_version,
            "memrelay.eval.experiment_id": self.experiment_id,
            "memrelay.eval.protocol_id": self.protocol_id,
            "memrelay.eval.run_id": self.run_id,
            "memrelay.eval.attempt_id": self.attempt_id,
            "memrelay.eval.scenario_id": self.scenario_id,
            "memrelay.eval.stratum_id": self.stratum_id,
            "memrelay.eval.history_mode": self.history_mode,
            "memrelay.eval.provider": self.provider,
            "memrelay.eval.credential_domain": self.credential_domain,
            "memrelay.eval.cost_source": self.cost_source,
            "memrelay.eval.evidence_class": self.evidence_class,
            "memrelay.eval.exposure_state": self.exposure_state,
            "memrelay.eval.failure_code": self.failure_code,
            "memrelay.eval.environment_fingerprint_sha256": self.environment_fingerprint_sha256,
        }


@dataclass(frozen=True, slots=True)
class SpanLink:
    """Opaque cross-process correlation; it is never fabricated parentage."""

    span_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        _require_code(self.span_id, "span_link.span_id")
        _require_code(self.correlation_id, "span_link.correlation_id")


@dataclass(frozen=True, slots=True)
class TelemetrySpan:
    """A canonical, secret-free projection of one local span."""

    span_id: str
    span_class: SpanClass
    context: TelemetryContext
    started_at: datetime
    ended_at: datetime
    attributes: Mapping[str, object] = field(default_factory=dict)
    links: Sequence[SpanLink] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_code(self.span_id, "span_id")
        if not isinstance(self.span_class, SpanClass):
            raise TelemetryConformanceError("unknown_span_class")
        started_at = _require_utc(self.started_at, "started_at")
        ended_at = _require_utc(self.ended_at, "ended_at")
        if ended_at < started_at:
            raise TelemetryConformanceError("invalid_telemetry_duration")
        safe_attributes = _validate_attributes(self.attributes)
        links = tuple(self.links)
        if len({(link.span_id, link.correlation_id) for link in links}) != len(links):
            raise TelemetryConformanceError("duplicate_span_link")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "ended_at", ended_at)
        object.__setattr__(self, "attributes", MappingProxyType(safe_attributes))
        object.__setattr__(self, "links", links)

    def to_record(self) -> dict[str, object]:
        return {
            "span_id": self.span_id,
            "span_class": self.span_class.value,
            "started_at": self.started_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "ended_at": self.ended_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "attributes": dict(self.attributes),
            "links": [
                {"span_id": link.span_id, "correlation_id": link.correlation_id}
                for link in self.links
            ],
            **self.context.attributes(),
        }


def _validate_attributes(attributes: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(attributes, Mapping):
        raise TelemetryConformanceError("invalid_telemetry_attributes")
    safe: dict[str, object] = {}
    for key, value in attributes.items():
        if key not in _SAFE_ATTRIBUTE_NAMES:
            raise TelemetryConformanceError("prohibited_or_unknown_telemetry_attribute")
        if isinstance(value, (bool, int)) or (isinstance(value, float) and math.isfinite(value)):
            safe[key] = value
        elif key in {"status_code", "transport_status"} and isinstance(value, str):
            safe[key] = _require_code(value, key)
        elif key == "artifact_sha256" and isinstance(value, str) and _SHA256.fullmatch(value):
            safe[key] = value
        else:
            raise TelemetryConformanceError("unsafe_telemetry_attribute_value", (key,))
    try:
        canonical_bytes(safe)
    except CanonicalizationError as error:
        raise TelemetryConformanceError("invalid_telemetry_attributes") from error
    return safe


def map_genai_development_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Translate only the frozen Development vocabulary; unknown aliases fail closed."""

    if not isinstance(fields, Mapping):
        raise TelemetryConformanceError("invalid_genai_fields")
    mapped: dict[str, object] = {"memrelay.eval.genai_map_version": GENAI_MAP_VERSION}
    for key, value in fields.items():
        if key not in _GENAI_FIELDS:
            raise TelemetryConformanceError("unknown_genai_development_field")
        target = _GENAI_FIELDS[key]
        if target.endswith("tokens"):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise TelemetryConformanceError("invalid_genai_development_value", (key,))
        elif not isinstance(value, str) or not value.isascii() or len(value) > 128:
            raise TelemetryConformanceError("invalid_genai_development_value", (key,))
        mapped[target] = value
    return mapped
