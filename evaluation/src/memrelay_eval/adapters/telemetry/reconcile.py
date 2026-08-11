"""Fail-closed telemetry delivery reconciliation with raw-order preservation."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import TelemetryConformanceError
from memrelay_eval.domain.ports import ArtifactStorePort

from .semantics import REQUIRED_SPAN_CLASSES, TELEMETRY_SCHEMA_VERSION, SpanClass, TelemetrySpan


@dataclass(frozen=True, slots=True)
class TelemetryReconciliation:
    """Auditable raw delivery facts and a deterministic, non-authoritative projection."""

    schema_version: str
    raw_span_ids: tuple[str, ...]
    canonical_span_ids: tuple[str, ...]
    duplicate_span_ids: tuple[str, ...]
    missing_classes: tuple[str, ...]
    failure_codes: tuple[str, ...]
    canonical_projection_digest: str

    @property
    def complete(self) -> bool:
        return not self.failure_codes and not self.missing_classes

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "raw_span_ids": list(self.raw_span_ids),
            "canonical_span_ids": list(self.canonical_span_ids),
            "duplicate_span_ids": list(self.duplicate_span_ids),
            "missing_classes": list(self.missing_classes),
            "failure_codes": list(self.failure_codes),
            "canonical_projection_digest": self.canonical_projection_digest,
        }


def reconcile_telemetry(
    spans: Sequence[TelemetrySpan],
    *,
    expected_order: Sequence[str] = (),
    observed_order: Sequence[str] | None = None,
    expected_classes: Collection[SpanClass] | None = None,
    partial_success: bool = False,
    collector_shutdown_verified: bool = False,
) -> TelemetryReconciliation:
    """Compare raw transport to frozen requirements without favorable-source selection."""

    raw = tuple(spans)
    by_id: dict[str, TelemetrySpan] = {}
    duplicates: list[str] = []
    failure_codes: set[str] = set()
    for span in raw:
        if span.context.schema_version != TELEMETRY_SCHEMA_VERSION:
            raise TelemetryConformanceError("unknown_telemetry_schema_version")
        existing = by_id.get(span.span_id)
        if existing is None:
            by_id[span.span_id] = span
        elif existing.to_record() == span.to_record():
            duplicates.append(span.span_id)
            failure_codes.add("TEL-DUPLICATE")
        else:
            duplicates.append(span.span_id)
            failure_codes.add("TEL-DUPLICATE")
            failure_codes.add("TEL-FAILURE")

    raw_ids = tuple(span.span_id for span in raw)
    canonical = tuple(sorted(by_id.values(), key=lambda item: item.span_id))
    classes = {span.span_class for span in canonical}
    required_classes = (
        REQUIRED_SPAN_CLASSES if expected_classes is None else frozenset(expected_classes)
    )
    if not required_classes.issubset(REQUIRED_SPAN_CLASSES):
        raise TelemetryConformanceError("unknown_expected_span_class")
    missing_classes = tuple(sorted(item.value for item in required_classes.difference(classes)))
    if missing_classes:
        failure_codes.add("TEL-DROP")
    if partial_success:
        failure_codes.add("TEL-PRIMARY")
    if not collector_shutdown_verified:
        failure_codes.add("TEL-FAILURE")
    order = tuple(expected_order)
    if order:
        actual_order = (
            tuple(observed_order)
            if observed_order is not None
            else tuple(item for item in raw_ids if item in set(order))
        )
        if actual_order != order:
            failure_codes.add("TEL-OUT-OF-ORDER")
    projection = {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "spans": [span.to_record() for span in canonical],
        "expected_classes": sorted(item.value for item in required_classes),
        "missing_classes": list(missing_classes),
    }
    return TelemetryReconciliation(
        schema_version=TELEMETRY_SCHEMA_VERSION,
        raw_span_ids=raw_ids,
        canonical_span_ids=tuple(span.span_id for span in canonical),
        duplicate_span_ids=tuple(sorted(set(duplicates))),
        missing_classes=missing_classes,
        failure_codes=tuple(sorted(failure_codes)),
        canonical_projection_digest=canonical_digest(projection),
    )


def telemetry_evidence_bytes(
    spans: Sequence[TelemetrySpan], reconciliation: TelemetryReconciliation
) -> bytes:
    """Serialize raw transport order plus a canonical projection as immutable CAS evidence."""

    raw = tuple(spans)
    canonical = tuple(
        sorted({span.span_id: span for span in raw}.values(), key=lambda item: item.span_id)
    )
    return canonical_bytes(
        {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "spans": [span.to_record() for span in canonical],
            "raw_spans": [span.to_record() for span in raw],
            "reconciliation": reconciliation.to_record(),
        }
    )


def persist_telemetry_evidence(
    store: ArtifactStorePort,
    spans: Sequence[TelemetrySpan],
    reconciliation: TelemetryReconciliation,
) -> ArtifactRef:
    """Store telemetry bodies in CAS only; ledger callers receive the resulting reference."""

    return store.put_bytes(
        telemetry_evidence_bytes(spans, reconciliation),
        media_type="application/json",
        classification="telemetry_evidence",
    )
