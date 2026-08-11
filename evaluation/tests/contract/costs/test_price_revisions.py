from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger
from memrelay_eval.domain.identity import framework_openai_identity
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
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    IntentMetadata,
)
from memrelay_eval.evidence.costs import CostRecord, cost_record_bytes
from memrelay_eval.evidence.pricing import (
    PriceRate,
    PriceTable,
    QuantityPricingInput,
    publish_monetary_view,
    publish_price_table,
    reprice_retained_quantity,
)

NOW = datetime(2026, 8, 10, 20, tzinfo=UTC)


def _table() -> PriceTable:
    return PriceTable(
        "framework_openai_test_20260810",
        "openai",
        "framework_openai_api",
        "gpt-4.1-mini-2025-04-14",
        datetime(2025, 4, 14, tzinfo=UTC),
        datetime(2030, 1, 1, tzinfo=UTC),
        "global",
        "USD",
        1_000_000,
        "excluded",
        "published_price_table",
        "fixture_openai_pricing",
        sha256(b"fixture pricing source").hexdigest(),
        datetime(2025, 4, 14, tzinfo=UTC),
        (PriceRate("input_token", "0.4"),),
    )


def test_monetary_view_is_cas_first_and_ledger_replay_is_idempotent() -> None:
    artifacts = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    experiment = ExperimentId.new()
    run = RunId.new()
    attempt = AttemptId.new()
    for intent in (
        CreateExperimentIntent(IntentMetadata(IntentId.new(), NOW), experiment, ProtocolId.new()),
        CreateRunIntent(IntentMetadata(IntentId.new(), NOW), run, experiment, AssignmentId.new()),
        CreateAttemptIntent(IntentMetadata(IntentId.new(), NOW), attempt, run),
    ):
        assert getattr(ledger.submit_intent(intent), "reason_code", None) is None

    source = artifacts.put_bytes(
        b"native usage", media_type="application/json", classification="native"
    )
    quantity_record = CostRecord(
        CostEntryId.new(),
        attempt,
        framework_openai_identity(),
        "native_provider",
        "framework_usage_1",
        source.sha256,
        1_000_000,
        "input_token",
        "metered",
        NOW,
        instrumentation_active=True,
    )
    quantity_artifact = artifacts.put_bytes(
        cost_record_bytes(quantity_record),
        media_type="application/json",
        classification="cost_quantity_evidence",
    )
    quantity = QuantityPricingInput(
        quantity_record,
        quantity_artifact,
        source,
        "gpt-4.1-mini-2025-04-14",
        "global",
        sha256(b"protocol").hexdigest(),
        sha256(b"environment").hexdigest(),
    )
    table = _table()
    table_artifact = publish_price_table(table, artifact_store=artifacts)
    view = reprice_retained_quantity(quantity, table=table, table_artifact=table_artifact)

    first = publish_monetary_view(
        view,
        run_id=run,
        quantity=quantity,
        table_artifact=table_artifact,
        artifact_store=artifacts,
        ledger=ledger,
    )
    replay = publish_monetary_view(
        view,
        run_id=run,
        quantity=quantity,
        table_artifact=table_artifact,
        artifact_store=artifacts,
        ledger=ledger,
    )

    assert first == replay
    links = ledger.monetary_views_for(attempt)
    assert len(links) == 1
    assert links[0].price_table_ref == table_artifact
