from __future__ import annotations

from datetime import UTC, datetime

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.domain.entities import InclusionDecision, RunTransition, TelemetryObservation
from memrelay_eval.domain.errors import ArtifactIntegrityError, IneligibleEvidenceError
from memrelay_eval.domain.ids import InclusionId, RunId
from memrelay_eval.domain.states import InclusionStatus, RunState


def test_artifact_store_is_deterministic_immutable_and_verifies_content() -> None:
    store = InMemoryArtifactStore()
    first = store.put_bytes(b"stable", media_type="text/plain", classification="synthetic")
    second = store.put_bytes(b"stable", media_type="text/plain", classification="synthetic")
    assert first == second
    assert store.open_verified(first) == b"stable"
    with pytest.raises(ArtifactIntegrityError):
        store.open_verified(type(first)(first.artifact_id, first.sha256, first.size_bytes + 1))


def test_ledger_is_append_only_and_rejects_unpaid_inclusion() -> None:
    ledger = InMemoryLedger()
    run_id = RunId.new()
    transition = RunTransition(run_id, RunState.PLANNED, RunState.ASSIGNED, datetime.now(UTC))
    ledger.append_transition(transition)
    assert ledger.history(run_id) == (transition,)
    decision = InclusionDecision(
        InclusionId.new(),
        run_id,
        InclusionStatus.INCLUDED,
        "not available from fake evidence",
        "a" * 64,
        datetime.now(UTC),
    )
    with pytest.raises(IneligibleEvidenceError):
        ledger.append_inclusion(decision)
    assert ledger.eligible_for_paid_or_study is False


def test_fakes_identify_unpaid_conformance_and_redact_telemetry() -> None:
    telemetry = InMemoryTelemetry()
    telemetry.emit(
        TelemetryObservation(
            "treatment_payload",
            datetime.now(UTC),
            {
                "duration_ms": 12,
                "prompt": "private prompt",
                "repository": "private/repo",
                "provider_payload": "private",
                "safe_text": "also dropped by default",
            },
        )
    )
    observation = telemetry.observations[0]
    assert observation.event_name == "redacted_event"
    assert dict(observation.attributes) == {"duration_ms": 12}
    assert telemetry.eligible_for_paid_or_study is False
