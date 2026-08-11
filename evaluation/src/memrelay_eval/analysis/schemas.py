"""Versioned Arrow schemas for the reconciled terminal analysis boundary."""

from __future__ import annotations

from hashlib import sha256
from typing import Final

import pyarrow as pa

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.errors import MaterializationError

PARQUET_SCHEMA_VERSION: Final = "1.0.0"
ANALYSIS_SCHEMA_VERSION: Final = "1.0.0"
PYARROW_VERSION: Final = "25.0.0"
ASSIGNED_UNITS_TABLE: Final = "assigned_units"
ELIGIBLE_OUTCOMES_TABLE: Final = "eligible_outcomes"
SAFETY_SCHEMA_VERSION: Final = "1.0.0"
ITT_TABLE_ARTIFACT: Final = "assignment_aligned_itt_table"
ESTIMATOR_DECISION_ARTIFACT: Final = "frozen_estimator_decision"
DIAGNOSTIC_REPORT_ARTIFACT: Final = "assignment_balance_diagnostic_report"

_STATUS_CATEGORIES: Final = (
    "included",
    "excluded",
)
_STRATUM_CATEGORIES: Final = ("product", "direct_engine")
_HISTORY_CATEGORIES: Final = ("controlled", "dynamic")
_TERMINAL_CATEGORIES: Final = (
    "succeeded",
    "agent_failed",
    "timed_out",
    "provider_unavailable",
    "quota_exhausted",
    "grader_failed",
    "evidence_incomplete",
    "infrastructure_failed_pre_exposure",
    "infrastructure_failed_post_exposure",
    "cancelled_by_circuit_breaker",
)
_OUTCOME_STATUS_CATEGORIES: Final = ("eligible", "excluded", "missing", "unavailable")
_ATTRITION_CATEGORIES: Final = ("complete", "attrited", "missing", "excluded")
_EXPOSURE_CATEGORIES: Final = ("unexposed", "exposed", "ambiguous", "unknown", "contradictory")
_CONTAMINATION_CATEGORIES: Final = ("isolated", "contaminated", "unknown")
_OUTCOME_KIND_CATEGORIES: Final = ("numeric", "categorical")


def require_pyarrow_25() -> None:
    """Reject materialization under an unqualified Arrow runtime."""
    if pa.__version__ != PYARROW_VERSION:
        raise MaterializationError("pyarrow_version_mismatch")


def dictionary_type() -> pa.DataType:
    """Use a stable index width for every categorical field."""
    return pa.dictionary(pa.int8(), pa.string())


def assigned_units_schema() -> pa.Schema:
    """Return the frozen assigned-unit denominator schema."""
    return _schema(
        ASSIGNED_UNITS_TABLE,
        (
            pa.field("schema_version", pa.string(), nullable=False),
            pa.field("dataset_version", pa.string(), nullable=False),
            pa.field("experiment_id", pa.string(), nullable=False),
            pa.field("protocol_sha256", pa.string(), nullable=False),
            pa.field("population_id", pa.string(), nullable=False),
            pa.field("assignment_id", pa.string(), nullable=False),
            pa.field("analysis_unit_id", pa.string(), nullable=False),
            pa.field("run_id", pa.string(), nullable=False),
            pa.field("attempt_id", pa.string(), nullable=False),
            pa.field("task_id", pa.string(), nullable=False),
            pa.field("replicate_id", pa.string(), nullable=False),
            pa.field("history_id", pa.string(), nullable=False),
            pa.field("sequence_id", pa.string(), nullable=False),
            pa.field("repository_id", pa.string(), nullable=False),
            pa.field("model_id", pa.string(), nullable=False),
            pa.field("environment_fingerprint_sha256", pa.string(), nullable=False),
            pa.field("stratum", dictionary_type(), nullable=False),
            pa.field("history_mode", dictionary_type(), nullable=False),
            pa.field("terminal_kind", dictionary_type(), nullable=False),
            pa.field("inclusion_status", dictionary_type(), nullable=False),
            pa.field("inclusion_reason", pa.string(), nullable=False),
            pa.field("reconciliation_sha256", pa.string(), nullable=False),
            pa.field("attrition_status", dictionary_type(), nullable=False),
            pa.field("exposure_status", dictionary_type(), nullable=False),
            pa.field("contamination_status", dictionary_type(), nullable=False),
            pa.field("failure_reason", pa.string(), nullable=True),
            pa.field("outcome_measurement_status", dictionary_type(), nullable=False),
            pa.field("started_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("terminal_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("reconciled_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field(
                "costs",
                pa.list_(
                    pa.struct(
                        (
                            pa.field("logical_ledger", pa.string(), nullable=False),
                            pa.field("unit", pa.string(), nullable=False),
                            pa.field("quantity", pa.decimal128(38, 10), nullable=True),
                            pa.field("availability", dictionary_type(), nullable=False),
                        )
                    )
                ),
                nullable=False,
            ),
            pa.field(
                "evidence",
                pa.list_(
                    pa.struct(
                        (
                            pa.field("artifact_id", pa.string(), nullable=False),
                            pa.field("sha256", pa.string(), nullable=False),
                            pa.field("size_bytes", pa.int64(), nullable=False),
                            pa.field("kind", pa.string(), nullable=False),
                            pa.field("manifest_sha256", pa.string(), nullable=False),
                        )
                    )
                ),
                nullable=False,
            ),
            pa.field("source_manifest_sha256", pa.list_(pa.string()), nullable=False),
        ),
        (
            "experiment_id",
            "stratum",
            "history_mode",
            "sequence_id",
            "assignment_id",
            "run_id",
            "attempt_id",
        ),
    )


def eligible_outcomes_schema() -> pa.Schema:
    """Return the frozen confirmatory-eligible endpoint schema."""
    return _schema(
        ELIGIBLE_OUTCOMES_TABLE,
        (
            pa.field("schema_version", pa.string(), nullable=False),
            pa.field("dataset_version", pa.string(), nullable=False),
            pa.field("experiment_id", pa.string(), nullable=False),
            pa.field("protocol_sha256", pa.string(), nullable=False),
            pa.field("population_id", pa.string(), nullable=False),
            pa.field("assignment_id", pa.string(), nullable=False),
            pa.field("analysis_unit_id", pa.string(), nullable=False),
            pa.field("run_id", pa.string(), nullable=False),
            pa.field("attempt_id", pa.string(), nullable=False),
            pa.field("task_id", pa.string(), nullable=False),
            pa.field("replicate_id", pa.string(), nullable=False),
            pa.field("history_id", pa.string(), nullable=False),
            pa.field("sequence_id", pa.string(), nullable=False),
            pa.field("repository_id", pa.string(), nullable=False),
            pa.field("model_id", pa.string(), nullable=False),
            pa.field("environment_fingerprint_sha256", pa.string(), nullable=False),
            pa.field("stratum", dictionary_type(), nullable=False),
            pa.field("history_mode", dictionary_type(), nullable=False),
            pa.field("endpoint_id", pa.string(), nullable=False),
            pa.field("outcome_id", pa.string(), nullable=False),
            pa.field("outcome_kind", dictionary_type(), nullable=False),
            pa.field("numeric_value", pa.float64(), nullable=True),
            pa.field("category_value", pa.string(), nullable=True),
            pa.field("unit", pa.string(), nullable=False),
            pa.field("evidence_sha256", pa.list_(pa.string()), nullable=False),
            pa.field("reconciliation_sha256", pa.string(), nullable=False),
        ),
        (
            "experiment_id",
            "stratum",
            "history_mode",
            "sequence_id",
            "assignment_id",
            "run_id",
            "attempt_id",
            "endpoint_id",
            "outcome_id",
        ),
    )


def schema_sha256(schema: pa.Schema) -> str:
    """Hash the exact Arrow serialization, including metadata and field ordering."""
    return sha256(schema.serialize().to_pybytes()).hexdigest()


def categories_for(field_name: str) -> tuple[str, ...]:
    """Expose the frozen category vocabulary used by canonical dictionary arrays."""
    categories = {
        "stratum": _STRATUM_CATEGORIES,
        "history_mode": _HISTORY_CATEGORIES,
        "terminal_kind": _TERMINAL_CATEGORIES,
        "inclusion_status": _STATUS_CATEGORIES,
        "attrition_status": _ATTRITION_CATEGORIES,
        "exposure_status": _EXPOSURE_CATEGORIES,
        "contamination_status": _CONTAMINATION_CATEGORIES,
        "outcome_measurement_status": _OUTCOME_STATUS_CATEGORIES,
        "outcome_kind": _OUTCOME_KIND_CATEGORIES,
        "availability": ("observed", "unavailable", "missing"),
    }
    try:
        return categories[field_name]
    except KeyError as error:
        raise MaterializationError("unknown_categorical_field") from error


def _schema(
    table_name: str, fields: tuple[pa.Field, ...], ordering_keys: tuple[str, ...]
) -> pa.Schema:
    categories = {
        name: list(categories_for(name))
        for name in (
            "stratum",
            "history_mode",
            "terminal_kind",
            "inclusion_status",
            "attrition_status",
            "exposure_status",
            "contamination_status",
            "outcome_measurement_status",
            "outcome_kind",
            "availability",
        )
        if name in {field.name for field in fields}
        or table_name == ASSIGNED_UNITS_TABLE
        and name == "availability"
    }
    metadata = {
        b"memrelay.schema.version": PARQUET_SCHEMA_VERSION.encode(),
        b"memrelay.table": table_name.encode(),
        b"memrelay.ordering_keys": canonical_bytes(ordering_keys),
        b"memrelay.categories": canonical_bytes(categories),
        b"memrelay.units": (
            b'{"costs.quantity":"preserved_native_unit","numeric_value":"endpoint_declared"}'
        ),
    }
    return pa.schema(fields, metadata=metadata)
