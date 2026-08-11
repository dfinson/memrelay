"""Telemetry-side sentinel evidence extraction without product-adapter imports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from memrelay_eval.domain.observation import (
    ObservationBoundary,
    ObservationPath,
    ObservationSentinel,
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
    expected_sentinels: Sequence[ObservationSentinel],
    collector_shutdown_verified: bool,
) -> ObservationTelemetryEvidence:
    """Require daemon-dispatch sentinel spans without treating transport alone as proof."""

    target_spans: list[TelemetrySpan] = []
    malformed = False
    mixed_path = False
    for span in spans:
        if span.span_class is not SpanClass.DAEMON_DISPATCH:
            continue
        attributes = span.attributes
        is_observation_span = any(
            key in attributes
            for key in ("sentinel_id", "sentinel_sequence", "observation_path", "restart_epoch")
        )
        if not is_observation_span:
            continue
        identifier = attributes.get("sentinel_id")
        sequence = attributes.get("sentinel_sequence")
        span_path = attributes.get("observation_path")
        restart_epoch = attributes.get("restart_epoch", 0)
        valid = (
            isinstance(identifier, str)
            and isinstance(sequence, int)
            and not isinstance(sequence, bool)
            and isinstance(restart_epoch, int)
            and not isinstance(restart_epoch, bool)
        )
        if span_path != path.value:
            mixed_path = True
            continue
        if not valid:
            malformed = True
            continue
        target_spans.append(span)

    expected_order = tuple(span.span_id for span in target_spans)
    reconciliation = reconcile_telemetry(
        target_spans,
        expected_order=expected_order,
        expected_classes={SpanClass.DAEMON_DISPATCH},
        collector_shutdown_verified=collector_shutdown_verified,
    )
    records: list[SentinelBoundaryRecord] = []
    for span in target_spans:
        identifier = span.attributes.get("sentinel_id")
        sequence = span.attributes.get("sentinel_sequence")
        restart_epoch = span.attributes.get("restart_epoch", 0)
        assert isinstance(identifier, str)
        assert isinstance(sequence, int) and not isinstance(sequence, bool)
        assert isinstance(restart_epoch, int) and not isinstance(restart_epoch, bool)
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
    expected_by_identifier = {
        sentinel.identifier: sentinel.sequence for sentinel in expected_sentinels
    }
    observed_counts: dict[str, int] = {}
    delivery_loss = False
    for record in records:
        expected_sequence = expected_by_identifier.get(record.sentinel_id)
        if expected_sequence != record.sequence:
            malformed = True
            continue
        observed_counts[record.sentinel_id] = observed_counts.get(record.sentinel_id, 0) + 1
    if (
        set(observed_counts) != set(expected_by_identifier)
        or any(count != 1 for count in observed_counts.values())
    ):
        delivery_loss = True

    failure_codes = set(reconciliation.failure_codes)
    if malformed:
        failure_codes.add("TEL-OBSERVATION-MALFORMED")
    if mixed_path:
        failure_codes.add("TEL-OBSERVATION-PATH-MISMATCH")
    if delivery_loss:
        failure_codes.add("TEL-OBSERVATION-DROP")
    return ObservationTelemetryEvidence(
        records=tuple(records),
        complete=(
            reconciliation.complete
            and not malformed
            and not mixed_path
            and not delivery_loss
            and len(records) == len(expected_order)
        ),
        failure_codes=tuple(
            sorted(
                failure_codes
                | (
                    {"TEL-OBSERVATION-RECONCILIATION"}
                    if malformed or mixed_path
                    else set()
                )
            )
        ),
    )
