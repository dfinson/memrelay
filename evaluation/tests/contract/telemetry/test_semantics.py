from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryTelemetry
from memrelay_eval.adapters.telemetry.reconcile import (
    persist_telemetry_evidence,
    reconcile_telemetry,
)
from memrelay_eval.adapters.telemetry.semantics import (
    GENAI_MAP_VERSION,
    REQUIRED_SPAN_CLASSES,
    TELEMETRY_SCHEMA_VERSION,
    SpanClass,
    SpanLink,
    TelemetryContext,
    TelemetrySpan,
    map_genai_development_fields,
)
from memrelay_eval.domain.errors import TelemetryConformanceError


def _context(**overrides: str) -> TelemetryContext:
    values = {
        "experiment_id": "exp_" + "1" * 32,
        "protocol_id": "protocol_" + "2" * 32,
        "run_id": "run_" + "3" * 32,
        "attempt_id": "attempt_" + "4" * 32,
        "scenario_id": "scenario_" + "5" * 32,
        "stratum_id": "product",
        "history_mode": "controlled",
        "provider": "github_copilot_sdk",
        "credential_domain": "github_copilot_subscription",
        "cost_source": "copilot_subscription_usage",
        "evidence_class": "native_evidence",
        "exposure_state": "unexposed",
        "environment_fingerprint_sha256": "a" * 64,
    }
    values.update(overrides)
    return TelemetryContext(**values)


def _span(
    span_class: SpanClass = SpanClass.CONTROL_ASSIGNMENT, **overrides: object
) -> TelemetrySpan:
    values: dict[str, object] = {
        "span_id": "span-1",
        "span_class": span_class,
        "context": _context(),
        "started_at": datetime(2026, 8, 10, tzinfo=UTC),
        "ended_at": datetime(2026, 8, 10, tzinfo=UTC) + timedelta(milliseconds=10),
        "attributes": {"duration_ms": 10, "input_tokens": 4, "output_tokens": 2},
    }
    values.update(overrides)
    return TelemetrySpan(**values)


def test_required_span_registry_and_canonical_schema_are_frozen() -> None:
    assert {item.value for item in REQUIRED_SPAN_CLASSES} == {
        "control.assignment",
        "provisioning",
        "cleanup",
        "copilot.session",
        "copilot.model_request",
        "mcp.tool_request",
        "daemon.dispatch",
        "memory.write",
        "memory.retrieval",
        "framework.extraction",
        "framework.embedding",
        "grader.execution",
        "judge.adjudication",
        "artifact.persistence",
        "inspect.export",
        "cost.reconciliation",
        "evidence.reconciliation",
    }
    schema_path = Path(__file__).parents[3] / "schemas" / "telemetry-evidence.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    record = _span().to_record()
    jsonschema.Draft202012Validator(schema).validate(
        {"schema_version": TELEMETRY_SCHEMA_VERSION, "spans": [record]}
    )
    assert record["memrelay.eval.attempt_id"] == _context().attempt_id


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("schema_version", "1.0.1", "unknown_telemetry_schema_version"),
        ("attempt_id", "run_" + "4" * 32, "invalid_telemetry_identity"),
        ("history_mode", "CONTROLLED", "invalid_telemetry_field"),
        ("provider", "GitHub_Copilot_SDK", "invalid_telemetry_field"),
        ("provider", "tr\u0435atment", "invalid_telemetry_field"),
        ("failure_code", "sk-" + "a" * 32, "secret_or_treatment_in_telemetry"),
    ],
)
def test_context_rejects_version_identity_and_unicode_alias_drift(
    field: str, value: str, code: str
) -> None:
    with pytest.raises(TelemetryConformanceError) as error:
        _context(**{field: value})
    assert error.value.code == code


def test_span_rejects_class_unit_confusion_duplicate_links_and_secret_attributes() -> None:
    with pytest.raises(TelemetryConformanceError, match="unknown span class"):
        _span(span_class="memory.write")  # type: ignore[arg-type]
    with pytest.raises(TelemetryConformanceError) as secret:
        _span(attributes={"prompt": "synthetic-canary-" + "a" * 32})
    assert secret.value.code == "prohibited_or_unknown_telemetry_attribute"
    link = SpanLink("source-1", "correlation-1")
    with pytest.raises(TelemetryConformanceError) as duplicate:
        _span(links=(link, link))
    assert duplicate.value.code == "duplicate_span_link"


def test_genai_development_fields_only_pass_through_frozen_mapper() -> None:
    mapped = map_genai_development_fields(
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-4.1-mini-2025-04-14",
            "gen_ai.usage.input_tokens": 3,
            "gen_ai.usage.output_tokens": 5,
        }
    )
    assert mapped["memrelay.eval.genai_map_version"] == GENAI_MAP_VERSION
    assert mapped["memrelay.eval.genai.input_tokens"] == 3
    with pytest.raises(TelemetryConformanceError) as alias:
        map_genai_development_fields({"GEN_AI.OPERATION.NAME": "chat"})
    assert alias.value.code == "unknown_genai_development_field"
    with pytest.raises(TelemetryConformanceError) as malformed:
        map_genai_development_fields({"gen_ai.usage.input_tokens": "3"})
    assert malformed.value.code == "invalid_genai_development_value"


def test_deterministic_fake_and_cas_persist_only_value_safe_telemetry() -> None:
    span = _span()
    fake = InMemoryTelemetry()
    fake.emit_span(span)
    assert fake.semantic_spans == (span,)
    assert fake.flush(0)["semantic_flushed"] == 1

    reconciliation = reconcile_telemetry((span,), collector_shutdown_verified=True)
    store = InMemoryArtifactStore()
    artifact = persist_telemetry_evidence(store, (span,), reconciliation)
    payload = store.open_verified(artifact)
    assert b"synthetic-canary" not in payload
    assert b"memrelay.eval.attempt_id" in payload
