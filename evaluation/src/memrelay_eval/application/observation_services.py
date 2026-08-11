"""Application composition for path-scoped observation qualification."""

from __future__ import annotations

from datetime import datetime

from memrelay_eval.adapters.memrelay.observation import (
    ObservationQualificationDecision,
    ObservationQualificationService,
)
from memrelay_eval.domain.observation import ObservationContract, ObservationEvidence


def qualify_observation(
    contract: ObservationContract,
    evidence: ObservationEvidence,
    *,
    decided_at: datetime,
) -> ObservationQualificationDecision:
    """Apply the observation adapter without exposing adapters to the CLI boundary."""

    return ObservationQualificationService().qualify(
        contract,
        evidence,
        decided_at=decided_at,
    )
