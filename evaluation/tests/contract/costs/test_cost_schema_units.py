from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import jsonschema
import pytest
from memrelay_eval.domain.errors import AuthorityConflictError
from memrelay_eval.domain.identity import (
    copilot_identity,
    framework_openai_identity,
    identity_for_span_class,
    local_identity,
)
from memrelay_eval.domain.ids import AttemptId, CostEntryId
from memrelay_eval.evidence.costs import (
    NOT_APPLICABLE,
    CostRecord,
    UnitConversion,
    UnitConversionTable,
    aggregate_quantities,
    normalize_native_quantities,
)

NOW = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)
SOURCE_HASH = sha256(b"deterministic-native-usage").hexdigest()
LOCAL_CPU = local_identity("local_cpu")


def record(
    *,
    identity=LOCAL_CPU,
    source_authority: str = "native_local_counter",
    quantity: int | str = 4,
    unit: str = "cpu_second",
    measurement_status: str = "metered",
    instrumentation_active: bool = False,
    conversion_table_version: str = NOT_APPLICABLE,
    conversion_table_sha256: str = NOT_APPLICABLE,
) -> CostRecord:
    return CostRecord(
        CostEntryId.new(),
        AttemptId.new(),
        identity,
        source_authority,
        "native_usage_1",
        SOURCE_HASH,
        quantity,
        unit,
        measurement_status,
        NOW,
        conversion_table_version=conversion_table_version,
        conversion_table_sha256=conversion_table_sha256,
        instrumentation_active=instrumentation_active,
    )


def test_cost_schema_accepts_canonical_raw_quantity_record() -> None:
    schema = json.loads(
        (Path(__file__).parents[3] / "schemas" / "cost-ledger.schema.json").read_text(
            encoding="utf-8"
        )
    )
    identity_schema = json.loads(
        (Path(__file__).parents[3] / "schemas" / "provider-identity.schema.json").read_text(
            encoding="utf-8"
        )
    )
    identity_schema_url = "https://memrelay.dev/evaluation/schemas/provider-identity.schema.json"
    resolver = jsonschema.RefResolver(
        (Path(__file__).parents[3] / "schemas").as_uri() + "/",
        schema,
        store={identity_schema_url: identity_schema},
    )
    jsonschema.Draft202012Validator(schema, resolver=resolver).validate(record().to_record())


@pytest.mark.parametrize(
    "quantity,status,instrumentation_active,reason",
    (
        (0, "metered", False, "invalid_cost_quantity"),
        ("unavailable", "metered", False, "invalid_unavailable_quantity"),
        ("unavailable", "unavailable", True, "invalid_unavailable_quantity"),
    ),
)
def test_missingness_and_observed_zero_are_not_interchangeable(
    quantity: int | str, status: str, instrumentation_active: bool, reason: str
) -> None:
    with pytest.raises(AuthorityConflictError) as error:
        record(
            quantity=quantity,
            measurement_status=status,
            instrumentation_active=instrumentation_active,
        )
    assert reason in error.value.fields


@pytest.mark.parametrize(
    ("identity", "authority", "unit"),
    (
        (copilot_identity(), "native_provider", "input_token"),
        (framework_openai_identity(), "native_provider", "output_token"),
        (local_identity("local_collector"), "native_control_clock", "wall_second"),
    ),
)
def test_provider_ledgers_have_disjoint_authority_and_unit_contracts(
    identity: object, authority: str, unit: str
) -> None:
    entry = record(identity=identity, source_authority=authority, unit=unit)  # type: ignore[arg-type]
    assert entry.identity.logical_ledger in {
        "copilot_subscription",
        "framework_openai",
        "local_resources",
    }


def test_cross_provider_tokens_and_unfrozen_unit_conversions_fail_closed() -> None:
    copilot = record(
        identity=copilot_identity(),
        source_authority="native_provider",
        unit="input_token",
    )
    openai = record(
        identity=framework_openai_identity(),
        source_authority="native_provider",
        unit="input_token",
    )
    with pytest.raises(AuthorityConflictError) as error:
        aggregate_quantities((copilot, openai), target_unit="input_token")
    assert "cross_authority_quantity_aggregation" in error.value.fields
    with pytest.raises(AuthorityConflictError) as error:
        aggregate_quantities((record(unit="wall_second"),), target_unit="cpu_second")
    assert "conversion_table_required" in error.value.fields


def test_only_hash_pinned_exact_conversion_can_change_unit() -> None:
    digest = sha256(b"conversion-table-v1").hexdigest()
    table = UnitConversionTable(
        "units_v1",
        digest,
        (UnitConversion("wall_second", "cpu_second", 2, 1),),
    )
    entry = record(
        unit="wall_second",
        conversion_table_version="units_v1",
        conversion_table_sha256=digest,
    )
    assert (
        aggregate_quantities((entry,), target_unit="cpu_second", conversion_table=table).quantity
        == 8
    )
    with pytest.raises(AuthorityConflictError) as error:
        aggregate_quantities(
            (record(unit="wall_second"),), target_unit="cpu_second", conversion_table=table
        )
    assert "conversion_authority_mismatch" in error.value.fields


def test_normalization_retains_exposed_zero_and_explicit_unavailable_provider_fields() -> None:
    records = normalize_native_quantities(
        attempt_id=AttemptId.new(),
        identity=copilot_identity(),
        source_authority="native_provider",
        source_ref="sdk_usage_1",
        source_sha256=SOURCE_HASH,
        observed_at=NOW,
        raw_quantities={"input_tokens": 0, "output_tokens": 7},
        instrumentation_active=True,
    )
    by_unit = {item.unit: item for item in records}
    assert by_unit["input_token"].quantity == 0
    assert by_unit["cached_input_token"].quantity == "unavailable"
    assert by_unit["output_token"].quantity == 7


def test_normalization_rejects_alias_case_or_unicode_native_field_names() -> None:
    for field in ("Input_Tokens", "input-tokens", "input_t\u043ekens"):
        with pytest.raises(AuthorityConflictError) as error:
            normalize_native_quantities(
                attempt_id=AttemptId.new(),
                identity=copilot_identity(),
                source_authority="native_provider",
                source_ref="sdk_usage_1",
                source_sha256=SOURCE_HASH,
                observed_at=NOW,
                raw_quantities={field: 1},
                instrumentation_active=True,
            )
        assert "unknown_native_quantity_field" in error.value.fields


def test_local_telemetry_actor_identity_does_not_replace_consumed_resource_identity() -> None:
    telemetry_identity = identity_for_span_class("control.assignment")
    resource_entry = record(identity=local_identity("local_cpu"), unit="cpu_second")
    assert telemetry_identity.resource_identity == "local_control"
    assert resource_entry.identity.resource_identity == "local_cpu"
