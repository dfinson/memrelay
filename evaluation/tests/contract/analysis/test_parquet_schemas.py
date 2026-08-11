from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pyarrow as pa
from memrelay_eval.analysis.schemas import (
    ANALYSIS_SCHEMA_VERSION,
    ASSIGNED_UNITS_TABLE,
    ELIGIBLE_OUTCOMES_TABLE,
    PARQUET_SCHEMA_VERSION,
    PYARROW_VERSION,
    SAFETY_SCHEMA_VERSION,
    assigned_units_schema,
    eligible_outcomes_schema,
    schema_sha256,
)


def test_versioned_arrow_schemas_pin_categories_units_and_ordering() -> None:
    assigned = assigned_units_schema()
    outcomes = eligible_outcomes_schema()

    assert pa.__version__ == PYARROW_VERSION
    assert assigned.metadata is not None
    assert assigned.metadata[b"memrelay.schema.version"] == PARQUET_SCHEMA_VERSION.encode()
    assert assigned.metadata[b"memrelay.table"] == ASSIGNED_UNITS_TABLE.encode()
    assert outcomes.metadata[b"memrelay.table"] == ELIGIBLE_OUTCOMES_TABLE.encode()
    quantity_type = assigned.field("costs").type.value_field.type.field("quantity").type
    assert quantity_type == pa.decimal128(38, 10)
    assert assigned.field("inclusion_status").type == pa.dictionary(pa.int8(), pa.string())
    assert outcomes.field("outcome_kind").type == pa.dictionary(pa.int8(), pa.string())
    assert schema_sha256(assigned) == sha256(assigned.serialize().to_pybytes()).hexdigest()


def test_committed_json_schema_versions_describe_the_parquet_boundary() -> None:
    schemas = Path(__file__).parents[3] / "schemas"
    for name in (
        "parquet-assigned-units.schema.json",
        "parquet-eligible-outcomes.schema.json",
        "parquet-dataset-manifest.schema.json",
        "analysis-derivation-manifest.schema.json",
        "eligible-outcome-authority.schema.json",
        "safety-report.schema.json",
        "task-audit-disposition.schema.json",
        "categorical-gate-decision.schema.json",
        "assignment-aligned-itt-table.schema.json",
        "frozen-estimator-decision.schema.json",
        "assignment-balance-diagnostic-report.schema.json",
        "frozen-claim-family.schema.json",
        "frozen-threshold-policy.schema.json",
        "sealed-claim-protocol.schema.json",
        "claim-gate-decision.schema.json",
        "frozen-power-protocol.schema.json",
        "power-evaluation.schema.json",
        "frozen-report-input.schema.json",
        "bounded-claim.schema.json",
        "release-fitness-decision.schema.json",
        "evidence-linked-report.schema.json",
    ):
        document = json.loads((schemas / name).read_text(encoding="utf-8"))
        assert document["properties"]["schema_version"]["const"] in {
            PARQUET_SCHEMA_VERSION,
            SAFETY_SCHEMA_VERSION,
            ANALYSIS_SCHEMA_VERSION,
        }


def test_claim_and_power_schemas_require_frozen_family_and_final_information_contracts() -> None:
    schemas = Path(__file__).parents[3] / "schemas"
    family = json.loads((schemas / "frozen-claim-family.schema.json").read_text(encoding="utf-8"))
    power = json.loads((schemas / "frozen-power-protocol.schema.json").read_text(encoding="utf-8"))
    evaluation = json.loads((schemas / "power-evaluation.schema.json").read_text(encoding="utf-8"))
    decision = json.loads((schemas / "claim-gate-decision.schema.json").read_text(encoding="utf-8"))
    seal = json.loads((schemas / "sealed-claim-protocol.schema.json").read_text(encoding="utf-8"))
    thresholds = json.loads(
        (schemas / "frozen-threshold-policy.schema.json").read_text(encoding="utf-8")
    )

    assert {"endpoint_scales", "efficiency_selection", "sealed_claim_protocol_sha256"} <= set(
        family["required"]
    )
    assert any(
        clause.get("then", {}).get("properties", {}).get("efficiency_selection", {}).get("const")
        == "intersection"
        for clause in family["allOf"]
    )
    assert {
        "family",
        "family_sha256",
        "power_endpoint_id",
        "estimator_version",
        "estimator_registry_sha256",
        "endpoint_estimand_fingerprints",
        "sealed_claim_protocol_sha256",
    } <= set(power["required"])
    assert {"endpoint_target_effects", "endpoint_scales", "endpoint_baselines"} <= set(
        power["$defs"]["cell"]["required"]
    )
    assert {
        "claim_id",
        "information_sha256",
        "power_evaluation_sha256",
        "sealed_claim_protocol_sha256",
        "categorical_policy_sha256",
        "categorical_gate_decision_sha256",
    } <= set(decision["required"])
    assert {
        "family_sha256",
        "cells",
        "independent_spot_check_sha256",
        "sealed_claim_protocol_sha256",
    } <= set(evaluation["required"])
    assert {"pre_enrollment_authorization_sha256", "registrations"} <= set(seal["required"])
    assert {
        "family_registration_sha256",
        "sealed_claim_protocol_sha256",
        "categorical_policy_sha256",
        "categorical_scope_id",
    } <= set(thresholds["required"])
    assert decision["$defs"]["sha256"]["pattern"] == "^[a-f0-9]{64}$"
