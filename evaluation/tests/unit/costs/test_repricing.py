from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.domain.errors import AuthorityConflictError
from memrelay_eval.domain.identity import framework_openai_identity
from memrelay_eval.domain.ids import AttemptId, CostEntryId
from memrelay_eval.evidence.costs import NOT_APPLICABLE, CostRecord, cost_record_bytes
from memrelay_eval.evidence.pricing import (
    PriceRate,
    PriceTable,
    QuantityPricingInput,
    load_price_table,
    publish_price_table,
    reprice_retained_quantities,
    reprice_retained_quantity,
    select_monetary_views,
    validate_price_table_set,
)

NOW = datetime(2026, 8, 10, 20, tzinfo=UTC)
PROTOCOL_HASH = sha256(b"protocol").hexdigest()
ENVIRONMENT_HASH = sha256(b"environment").hexdigest()


def _source(store: InMemoryArtifactStore):
    return store.put_bytes(
        b"native framework usage",
        media_type="application/json",
        classification="native_usage",
    )


def _quantity(
    store: InMemoryArtifactStore,
    *,
    unit: str,
    amount: int | str,
    model: str = "gpt-4.1-mini-2025-04-14",
    conversion_table_version: str = NOT_APPLICABLE,
    conversion_table_sha256: str = NOT_APPLICABLE,
) -> QuantityPricingInput:
    source = _source(store)
    record = CostRecord(
        CostEntryId.new(),
        AttemptId.new(),
        framework_openai_identity(),
        "native_provider",
        "framework_usage_1",
        source.sha256,
        amount,
        unit,
        "unavailable" if amount == "unavailable" else "metered",
        NOW,
        conversion_table_version=conversion_table_version,
        conversion_table_sha256=conversion_table_sha256,
        instrumentation_active=amount != "unavailable",
    )
    quantity_artifact = store.put_bytes(
        cost_record_bytes(record),
        media_type="application/json",
        classification="cost_quantity_evidence",
    )
    return QuantityPricingInput(
        record,
        quantity_artifact,
        source,
        model,
        "global",
        PROTOCOL_HASH,
        ENVIRONMENT_HASH,
    )


def _frozen_table() -> PriceTable:
    path = Path(__file__).parents[3] / "catalog" / "prices" / "framework-openai-initial.json"
    data = path.read_bytes()
    return load_price_table(data, expected_sha256=sha256(data).hexdigest())


def test_frozen_framework_rates_reprice_exactly_without_binary_float() -> None:
    store = InMemoryArtifactStore()
    table = _frozen_table()
    table_artifact = publish_price_table(table, artifact_store=store)
    views = reprice_retained_quantities(
        (
            _quantity(store, unit="input_token", amount=2_000_000),
            _quantity(store, unit="cached_input_token", amount=3_000_000),
            _quantity(store, unit="output_token", amount=500_000),
        ),
        table=table,
        table_artifact=table_artifact,
    )

    assert [view.amount for view in views] == ["0.8", "0.3", "0.8"]
    assert all(view.monetary_authority == "estimated" for view in views)
    assert all(view.price_table_artifact_sha256 == table_artifact.sha256 for view in views)
    assert all(view.derivation_version == "1.0.0" for view in views)


def test_repricing_preserves_quantity_bytes_and_creates_distinct_revision_view() -> None:
    store = InMemoryArtifactStore()
    quantity = _quantity(store, unit="input_token", amount=1_000_000)
    original_quantity_bytes = cost_record_bytes(quantity.record)
    initial = _frozen_table()
    initial_artifact = publish_price_table(initial, artifact_store=store)
    original = reprice_retained_quantity(quantity, table=initial, table_artifact=initial_artifact)
    revised = replace(
        initial,
        version="framework_openai_revision_20260810",
        rates=(
            PriceRate("input_token", "0.5"),
            PriceRate("cached_input_token", "0.1"),
            PriceRate("output_token", "1.6"),
        ),
    )
    revised_artifact = publish_price_table(revised, artifact_store=store)
    replacement = reprice_retained_quantity(
        quantity, table=revised, table_artifact=revised_artifact
    )

    assert cost_record_bytes(quantity.record) == original_quantity_bytes
    assert original.amount == "0.4"
    assert replacement.amount == "0.5"
    assert original.id != replacement.id
    assert original.price_table_sha256 != replacement.price_table_sha256


@pytest.mark.parametrize(
    ("unit", "amount", "status"),
    (
        ("cache_write_token", 1, "not_applicable"),
        ("input_token", "unavailable", "unavailable"),
    ),
)
def test_unavailable_and_unpriced_quantities_remain_explicit(
    unit: str, amount: int | str, status: str
) -> None:
    store = InMemoryArtifactStore()
    table = _frozen_table()
    view = reprice_retained_quantity(
        _quantity(store, unit=unit, amount=amount),
        table=table,
        table_artifact=publish_price_table(table, artifact_store=store),
    )

    assert view.pricing_status == status
    assert view.amount is None


def test_repricing_fails_closed_on_model_conversion_or_overlapping_authority() -> None:
    store = InMemoryArtifactStore()
    table = _frozen_table()
    table_artifact = publish_price_table(table, artifact_store=store)

    with pytest.raises(AuthorityConflictError, match="authority_conflict"):
        reprice_retained_quantity(
            _quantity(store, unit="input_token", amount=1, model="gpt-4.1-mini-other"),
            table=table,
            table_artifact=table_artifact,
        )
    with pytest.raises(AuthorityConflictError, match="authority_conflict"):
        reprice_retained_quantity(
            _quantity(
                store,
                unit="input_token",
                amount=1,
                conversion_table_version="units_v1",
                conversion_table_sha256=sha256(b"units").hexdigest(),
            ),
            table=table,
            table_artifact=table_artifact,
        )
    with pytest.raises(AuthorityConflictError, match="authority_conflict"):
        validate_price_table_set((table, replace(table, version="same-time-different-version")))


def test_repricing_rejects_mutable_table_substitution_and_implicit_selection() -> None:
    store = InMemoryArtifactStore()
    table = _frozen_table()
    quantity = _quantity(store, unit="input_token", amount=1)

    with pytest.raises(AuthorityConflictError, match="authority_conflict"):
        reprice_retained_quantity(
            quantity,
            table=table,
            table_artifact=store.put_bytes(
                b"untrusted replacement",
                media_type="application/json",
                classification="price_table_evidence",
            ),
        )

    view = reprice_retained_quantity(
        quantity,
        table=table,
        table_artifact=publish_price_table(table, artifact_store=store),
    )
    with pytest.raises(AuthorityConflictError, match="authority_conflict"):
        select_monetary_views(
            (view,),
            price_table_sha256=sha256(b"unselected").hexdigest(),
            scenario_id="frozen_initial",
        )
