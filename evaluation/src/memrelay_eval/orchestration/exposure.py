"""Conservative exposure classification and deterministic port emission."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from threading import Lock

from memrelay_eval.domain.entities import (
    ExposureDecision,
    ExposureObservation,
    ExposureRecord,
    TelemetryObservation,
)
from memrelay_eval.domain.errors import (
    DurableConformanceRequiredError,
    ExposureAlreadyRecordedError,
)
from memrelay_eval.domain.ids import AssignmentId, AttemptId
from memrelay_eval.domain.ports import LedgerPort, TelemetryPort
from memrelay_eval.domain.states import ExposureClassification, ExposurePhase


class ExposureRecorder:
    """Writes one immutable exposure record to the domain ledger and telemetry ports."""

    def __init__(self, ledger: LedgerPort, telemetry: TelemetryPort) -> None:
        self._ledger = ledger
        self._telemetry = telemetry
        self._records: dict[AttemptId, ExposureRecord] = {}
        self._lock = Lock()

    def record(
        self,
        attempt_id: AttemptId,
        assignment_id: AssignmentId,
        observations: Sequence[ExposureObservation],
    ) -> ExposureRecord:
        decision = classify_exposure(observations)
        record = ExposureRecord(attempt_id, assignment_id, decision)
        with self._lock:
            if attempt_id in self._records:
                raise ExposureAlreadyRecordedError(ExposureAlreadyRecordedError.code)
            self._ledger.append_exposure_record(record)
            first_value = decision.first_monotonic_exposure_time
            self._telemetry.emit(
                TelemetryObservation(
                    "assignment_exposure",
                    observations[0].occurred_at if observations else _epoch(),
                    {
                        "evidence_count": len(decision.evidence_refs),
                        "is_exposed": decision.classification is ExposureClassification.EXPOSED,
                        "first_monotonic_exposure": (
                            first_value if first_value is not None else -1.0
                        ),
                    },
                )
            )
            self._records[attempt_id] = record
        return record

    def require_durable_execution(self) -> None:
        raise DurableConformanceRequiredError(DurableConformanceRequiredError.code)


def classify_exposure(observations: Sequence[ExposureObservation]) -> ExposureDecision:
    """Classify unknown, missing, malformed, and conflicting evidence as exposed."""
    ordered = tuple(observations)
    grouped: dict[ExposurePhase, ExposureObservation] = {}
    duplicate_or_invalid = False
    evidence = []
    confirmed_times: list[float] = []
    for observation in ordered:
        if not isinstance(observation.phase, ExposurePhase):
            duplicate_or_invalid = True
            continue
        if observation.phase in grouped:
            duplicate_or_invalid = True
        else:
            grouped[observation.phase] = observation
        evidence.extend(observation.evidence_refs)
        if (
            not observation.evidence_refs
            or not isinstance(observation.observed, bool)
            or not isinstance(observation.occurred_at, datetime)
            or observation.occurred_at.tzinfo is None
            or observation.occurred_at.utcoffset() is None
            or not isinstance(observation.monotonic_seconds, (int, float))
            or isinstance(observation.monotonic_seconds, bool)
            or not math.isfinite(observation.monotonic_seconds)
            or observation.monotonic_seconds < 0
        ):
            duplicate_or_invalid = True
            continue
        if observation.observed is True:
            confirmed_times.append(float(observation.monotonic_seconds))
    unique_evidence = tuple(dict.fromkeys(evidence))
    first = min(confirmed_times) if confirmed_times else None
    if set(grouped) != set(ExposurePhase):
        return ExposureDecision(
            ExposureClassification.EXPOSED,
            unique_evidence,
            "exposure_evidence_missing",
            first,
            ordered,
        )
    if duplicate_or_invalid:
        return ExposureDecision(
            ExposureClassification.EXPOSED,
            unique_evidence,
            "exposure_evidence_ambiguous",
            first,
            ordered,
        )
    if confirmed_times:
        return ExposureDecision(
            ExposureClassification.EXPOSED,
            unique_evidence,
            "exposure_observed",
            first,
            ordered,
        )
    return ExposureDecision(
        ExposureClassification.UNEXPOSED,
        unique_evidence,
        "conclusively_unexposed",
        None,
        ordered,
    )


def _epoch():
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC)
