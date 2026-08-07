from __future__ import annotations

from datetime import UTC, datetime

from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.states import ExposureClassification
from memrelay_eval.orchestration.exposure import (
    ExposureObservation,
    ExposurePhase,
    classify_exposure,
)


def test_ambiguous_duplicate_assignment_resolution_is_never_retry_eligible() -> None:
    evidence = ArtifactRef.from_bytes(b"fault evidence")
    observations = (
        ExposureObservation(
            ExposurePhase.ASSIGNMENT_RESOLUTION,
            False,
            datetime(2026, 8, 7, tzinfo=UTC),
            0.0,
            (evidence,),
        ),
        ExposureObservation(
            ExposurePhase.ASSIGNMENT_RESOLUTION,
            True,
            datetime(2026, 8, 7, tzinfo=UTC),
            1.0,
            (evidence,),
        ),
    )

    decision = classify_exposure(observations)

    assert decision.classification is ExposureClassification.EXPOSED
    assert decision.is_conclusively_unexposed is False
