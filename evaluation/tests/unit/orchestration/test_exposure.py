from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Barrier, Thread

from memrelay_eval.adapters.fakes import InMemoryLedger, InMemoryTelemetry
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import ExposureAlreadyRecordedError
from memrelay_eval.domain.ids import AssignmentId, AttemptId
from memrelay_eval.domain.states import ExposureClassification
from memrelay_eval.orchestration.exposure import (
    ExposureObservation,
    ExposurePhase,
    ExposureRecorder,
    classify_exposure,
)


def _observations(
    *,
    exposed_phase: ExposurePhase | None = None,
) -> tuple[ExposureObservation, ...]:
    evidence = ArtifactRef.from_bytes(b"exposure-evidence")
    start = datetime(2026, 8, 7, tzinfo=UTC)
    return tuple(
        ExposureObservation(
            phase=phase,
            observed=phase is exposed_phase,
            occurred_at=start + timedelta(seconds=index),
            monotonic_seconds=float(index),
            evidence_refs=(evidence,),
        )
        for index, phase in enumerate(ExposurePhase)
    )


def test_truth_table_is_fail_closed_and_records_first_monotonic_exposure() -> None:
    unexposed = classify_exposure(_observations())
    exposed = classify_exposure(_observations(exposed_phase=ExposurePhase.INFERENCE))
    missing = classify_exposure(_observations()[:-1])
    contradictory = classify_exposure(
        _observations()
        + (
            ExposureObservation(
                ExposurePhase.TASK_DELIVERY,
                True,
                datetime(2026, 8, 7, tzinfo=UTC),
                99.0,
                (ArtifactRef.from_bytes(b"conflict"),),
            ),
        )
    )

    assert unexposed.classification is ExposureClassification.UNEXPOSED
    assert unexposed.first_monotonic_exposure_time is None
    assert exposed.classification is ExposureClassification.EXPOSED
    assert exposed.first_monotonic_exposure_time == 3.0
    assert missing.classification is ExposureClassification.EXPOSED
    assert contradictory.classification is ExposureClassification.EXPOSED
    assert contradictory.first_monotonic_exposure_time == 99.0


def test_missing_evidence_and_invalid_timing_are_exposed() -> None:
    observations = list(_observations())
    observations[0] = ExposureObservation(
        ExposurePhase.ASSIGNMENT_RESOLUTION,
        False,
        datetime(2026, 8, 7, tzinfo=UTC),
        0.0,
        (),
    )
    observations[1] = ExposureObservation(
        ExposurePhase.MEMORY_PROVISION,
        False,
        datetime(2026, 8, 7, tzinfo=UTC),
        -1.0,
        (ArtifactRef.from_bytes(b"bad-timing"),),
    )

    decision = classify_exposure(tuple(observations))
    assert decision.classification is ExposureClassification.EXPOSED
    assert decision.reason_code in {"exposure_evidence_missing", "exposure_evidence_ambiguous"}


def test_recorder_appends_redacted_unpaid_records_to_both_ports() -> None:
    ledger = InMemoryLedger()
    telemetry = InMemoryTelemetry()
    recorder = ExposureRecorder(ledger, telemetry)

    record = recorder.record(AttemptId.new(), AssignmentId.new(), _observations())

    assert record.decision.classification is ExposureClassification.UNEXPOSED
    assert ledger.exposure_records == (record,)
    assert telemetry.observations[-1].event_name == "assignment_exposure"
    assert telemetry.eligible_for_paid_or_study is False


def test_concurrent_conflicting_exposure_reports_preserve_the_first_evidence() -> None:
    ledger = InMemoryLedger()
    telemetry = InMemoryTelemetry()
    recorder = ExposureRecorder(ledger, telemetry)
    attempt_id = AttemptId.new()
    assignment_id = AssignmentId.new()
    barrier = Barrier(2)
    results: list[object] = []

    def record(observations: tuple[ExposureObservation, ...]) -> None:
        barrier.wait(timeout=2)
        try:
            results.append(recorder.record(attempt_id, assignment_id, observations))
        except ExposureAlreadyRecordedError as error:
            results.append(error)

    first = Thread(target=record, args=(_observations(),))
    second = Thread(target=record, args=(_observations(exposed_phase=ExposurePhase.ACCESS),))
    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(ledger.exposure_records) == 1
    assert sum(isinstance(result, ExposureAlreadyRecordedError) for result in results) == 1
