from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256

from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger
from memrelay_eval.domain.identity import (
    copilot_identity,
    framework_openai_identity,
    local_identity,
)
from memrelay_eval.domain.ids import (
    AssignmentId,
    AttemptId,
    CostEntryId,
    ExperimentId,
    IntentId,
    ProtocolId,
    RunId,
)
from memrelay_eval.domain.intents import (
    CostLedgerIntent,
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    IntentMetadata,
)
from memrelay_eval.evidence.costs import CostRecord, cost_record_bytes, publish_cost_record
from memrelay_eval.ledger import SqliteLedger

NOW = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)


def seed(ledger: InMemoryLedger) -> tuple[RunId, AttemptId]:
    experiment_id = ExperimentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    for intent in (
        CreateExperimentIntent(
            IntentMetadata(IntentId.new(), NOW), experiment_id, ProtocolId.new()
        ),
        CreateRunIntent(
            IntentMetadata(IntentId.new(), NOW), run_id, experiment_id, AssignmentId.new()
        ),
        CreateAttemptIntent(IntentMetadata(IntentId.new(), NOW), attempt_id, run_id),
    ):
        assert getattr(ledger.submit_intent(intent), "reason_code", None) is None
    return run_id, attempt_id


def record(identity: object, authority: str, unit: str, attempt_id: AttemptId) -> CostRecord:
    return CostRecord(
        CostEntryId.new(),
        attempt_id,
        identity,  # type: ignore[arg-type]
        authority,
        "native_evidence_1",
        sha256(unit.encode()).hexdigest(),
        3,
        unit,
        "metered",
        NOW,
    )


def test_cost_records_publish_cas_artifacts_then_three_separate_ledger_links() -> None:
    artifact_store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    run_id, attempt_id = seed(ledger)
    records = (
        record(copilot_identity(), "native_provider", "input_token", attempt_id),
        record(framework_openai_identity(), "native_provider", "output_token", attempt_id),
        record(
            local_identity("local_storage"),
            "native_local_counter",
            "storage_read_operation",
            attempt_id,
        ),
    )
    artifacts = tuple(
        publish_cost_record(
            item,
            artifact_store=artifact_store,
            ledger=ledger,
            run_id=run_id,
            source_evidence=artifact_store.put_bytes(
                b"native usage", media_type="application/json", classification="native_usage"
            ),
        )
        for item in records
    )
    links = ledger.cost_ledger_entries_for(attempt_id)
    assert {item.logical_ledger for item in links} == {
        "copilot_subscription",
        "framework_openai",
        "local_resources",
    }
    assert {item.artifact_ref for item in links} == set(artifacts)
    assert all(artifact_store.open_verified(item) for item in artifacts)


def test_control_ledger_replays_one_cost_intent_without_duplicate_or_partial_link(
    tmp_path: object,
) -> None:
    artifact_store = InMemoryArtifactStore()
    ledger = SqliteLedger.open_control(tmp_path / "costs.sqlite")  # type: ignore[operator]
    run_id, attempt_id = seed(ledger)  # type: ignore[arg-type]
    entry = record(local_identity("local_cpu"), "native_local_counter", "cpu_second", attempt_id)
    artifact = artifact_store.put_bytes(
        cost_record_bytes(entry),
        media_type="application/json",
        classification="cost_quantity_evidence",
    )
    source = artifact_store.put_bytes(
        b"native usage",
        media_type="application/json",
        classification="native_usage",
    )
    metadata = IntentMetadata(
        IntentId.new(),
        NOW,
        source_attempt_id=attempt_id,
        evidence_refs=(artifact, source),
        reason_code="cost_quantity_recorded",
    )
    intent = CostLedgerIntent(
        metadata,
        entry.cost_entry_id,
        run_id,
        attempt_id,
        "local_resources",
        source,
        artifact,
    )
    first = ledger.submit_intent(intent)
    replay = ledger.submit_intent(intent)
    assert getattr(first, "reason_code", None) is None
    assert replay.idempotent is True
    duplicate = ledger.submit_intent(
        CostLedgerIntent(
            IntentMetadata(
                IntentId.new(),
                NOW,
                source_attempt_id=attempt_id,
                evidence_refs=(artifact, source),
                reason_code="cost_quantity_recorded",
            ),
            entry.cost_entry_id,
            run_id,
            attempt_id,
            "local_resources",
            source,
            artifact,
        )
    )
    assert getattr(duplicate, "reason_code", None) == "integrity_violation"
    assert len(ledger.cost_ledger_entries_for(attempt_id)) == 1
    ledger.close()


def test_control_ledger_serializes_concurrent_cost_intent_replay(tmp_path: object) -> None:
    artifact_store = InMemoryArtifactStore()
    ledger = SqliteLedger.open_control(tmp_path / "concurrent-costs.sqlite")  # type: ignore[operator]
    run_id, attempt_id = seed(ledger)  # type: ignore[arg-type]
    entry = record(local_identity("local_cpu"), "native_local_counter", "cpu_second", attempt_id)
    artifact = artifact_store.put_bytes(
        cost_record_bytes(entry),
        media_type="application/json",
        classification="cost_quantity_evidence",
    )
    source = artifact_store.put_bytes(
        b"native usage",
        media_type="application/json",
        classification="native_usage",
    )
    intent = CostLedgerIntent(
        IntentMetadata(
            IntentId.new(),
            NOW,
            source_attempt_id=attempt_id,
            evidence_refs=(artifact, source),
            reason_code="cost_quantity_recorded",
        ),
        entry.cost_entry_id,
        run_id,
        attempt_id,
        "local_resources",
        source,
        artifact,
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(ledger.submit_intent, (intent,) * 16))
    assert sum(result.idempotent for result in results) == 15
    assert len(ledger.cost_ledger_entries_for(attempt_id)) == 1
    ledger.close()
