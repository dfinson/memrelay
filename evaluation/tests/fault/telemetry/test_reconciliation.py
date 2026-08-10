from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from memrelay_eval.adapters.telemetry.otel import OtelTelemetry
from memrelay_eval.adapters.telemetry.reconcile import reconcile_telemetry
from memrelay_eval.adapters.telemetry.semantics import (
    REQUIRED_SPAN_CLASSES,
    TelemetryContext,
    TelemetrySpan,
)
from memrelay_eval.domain.errors import TelemetryConformanceError


def _context() -> TelemetryContext:
    return TelemetryContext(
        experiment_id="exp_" + "1" * 32,
        protocol_id="protocol_" + "2" * 32,
        run_id="run_" + "3" * 32,
        attempt_id="attempt_" + "4" * 32,
        scenario_id="scenario_" + "5" * 32,
        stratum_id="product",
        history_mode="controlled",
        provider="github_copilot_sdk",
        credential_domain="github_copilot_subscription",
        cost_source="copilot_subscription_usage",
        evidence_class="native_evidence",
        exposure_state="unexposed",
        failure_code="none",
        environment_fingerprint_sha256="a" * 64,
    )


def _spans() -> tuple[TelemetrySpan, ...]:
    started = datetime(2026, 8, 10, tzinfo=UTC)
    return tuple(
        TelemetrySpan(
            span_id=f"span-{index}",
            span_class=span_class,
            context=_context(),
            started_at=started + timedelta(milliseconds=index),
            ended_at=started + timedelta(milliseconds=index + 1),
            attributes={"duration_ms": 1, "input_tokens": index, "output_tokens": index},
        )
        for index, span_class in enumerate(
            sorted(REQUIRED_SPAN_CLASSES, key=lambda item: item.value)
        )
    )


def test_complete_delivery_requires_all_classes_order_and_shutdown_proof() -> None:
    spans = _spans()
    result = reconcile_telemetry(
        spans,
        expected_order=tuple(span.span_id for span in spans),
        collector_shutdown_verified=True,
    )
    assert result.complete
    assert result.missing_classes == ()
    assert set(result.raw_span_ids) == set(result.canonical_span_ids)


def test_drop_duplicate_out_of_order_partial_and_favorable_conflict_all_block() -> None:
    spans = _spans()
    dropped = reconcile_telemetry(spans[:-1], collector_shutdown_verified=True)
    assert "TEL-DROP" in dropped.failure_codes

    duplicate = reconcile_telemetry(
        spans + (spans[0],),
        expected_order=tuple(span.span_id for span in spans),
        collector_shutdown_verified=True,
    )
    assert "TEL-DUPLICATE" in duplicate.failure_codes
    assert duplicate.raw_span_ids.count(spans[0].span_id) == 2
    assert duplicate.canonical_span_ids.count(spans[0].span_id) == 1

    reordered = reconcile_telemetry(
        tuple(reversed(spans)),
        expected_order=tuple(span.span_id for span in spans),
        collector_shutdown_verified=True,
    )
    assert "TEL-OUT-OF-ORDER" in reordered.failure_codes

    partial = reconcile_telemetry(spans, partial_success=True, collector_shutdown_verified=True)
    assert "TEL-PRIMARY" in partial.failure_codes

    conflicting = TelemetrySpan(
        span_id=spans[0].span_id,
        span_class=spans[0].span_class,
        context=spans[0].context,
        started_at=spans[0].started_at,
        ended_at=spans[0].ended_at,
        attributes={"duration_ms": 2},
    )
    conflict = reconcile_telemetry(spans + (conflicting,), collector_shutdown_verified=True)
    assert {"TEL-DUPLICATE", "TEL-FAILURE"}.issubset(conflict.failure_codes)
    assert not conflict.complete


def test_cancellation_timeout_crash_and_concurrent_replay_remain_visible() -> None:
    spans = _spans()
    failed_shutdown = reconcile_telemetry(spans, collector_shutdown_verified=False)
    assert "TEL-FAILURE" in failed_shutdown.failure_codes

    telemetry = OtelTelemetry("http://127.0.0.1:4318", exporter=lambda spans, timeout: True)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(telemetry.emit_span, spans))
    assert {span.span_id for span in telemetry.spans} == {span.span_id for span in spans}
    assert telemetry.flush(0)["flushed"] == len(spans)
    with pytest.raises(TelemetryConformanceError):
        telemetry.flush(-1)
