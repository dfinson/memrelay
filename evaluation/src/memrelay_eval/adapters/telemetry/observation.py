"""Telemetry-side sentinel evidence extraction without product-adapter imports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from memrelay_eval.domain.observation import (
    ObservationBoundary,
    ObservationPath,
    SentinelBoundaryRecord,
)

from .reconcile import reconcile_telemetry
from .semantics import SpanClass, TelemetrySpan


@dataclass(frozen=True, slots=True)
class ObservationTelemetryEvidence:
    """Telemetry boundary facts extracted from value-safe sentinel span attributes."""

    records: tuple[SentinelBoundaryRecord, ...]
    complete: bool
    failure_codes: tuple[str, ...]


def observation_telemetry_evidence(
    spans: Sequence[TelemetrySpan],
    *,
    path: ObservationPath,
    collector_shutdown_verified: bool,
) -> ObservationTelemetryEvidence:
    """Require daemon-dispatch sentinel spans without treating transport alone as proof."""

    expected_order = tuple(
        span.span_id for span in spans if span.span_class is SpanClass.DAEMON_DISPATCH
    )
    reconciliation = reconcile_telemetry(
        spans,
        expected_order=expected_order,
        expected_classes={SpanClass.DAEMON_DISPATCH},
        collector_shutdown_verified=collector_shutdown_verified,
    )
    records: list[SentinelBoundaryRecord] = []
    for span in spans:
        if span.span_class is not SpanClass.DAEMON_DISPATCH:
            continue
        identifier = span.attributes.get("sentinel_id")
        sequence = span.attributes.get("sentinel_sequence")
        span_path = span.attributes.get("observation_path")
        restart_epoch = span.attributes.get("restart_epoch", 0)
        if (
            not isinstance(identifier, str)
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or span_path != path.value
            or not isinstance(restart_epoch, int)
            or isinstance(restart_epoch, bool)
        ):
            continue
        records.append(
            SentinelBoundaryRecord(
                path=path,
                boundary=ObservationBoundary.TELEMETRY,
                sentinel_id=identifier,
                sequence=sequence,
                observed_at=span.ended_at,
                restart_epoch=restart_epoch,
            )
        )
    return ObservationTelemetryEvidence(
        records=tuple(records),
        complete=reconciliation.complete and len(records) == len(expected_order),
        failure_codes=reconciliation.failure_codes,
    )
