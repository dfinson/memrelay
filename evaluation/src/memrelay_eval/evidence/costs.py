"""Immutable quantity evidence and thin, control-owned cost-ledger publication."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import AuthorityConflictError, SecretBoundaryViolationError
from memrelay_eval.domain.identity import PROVIDER_IDENTITY_SCHEMA_VERSION, ProviderIdentity
from memrelay_eval.domain.ids import AttemptId, CostEntryId, IntentId, RunId
from memrelay_eval.domain.intents import (
    AuthorityConflictIntent,
    CostLedgerIntent,
    IntentMetadata,
)
from memrelay_eval.domain.ports import ArtifactStorePort, LedgerPort
from memrelay_eval.evidence.secret_scan import SecretScanFinding, scan_secret_boundaries

COST_LEDGER_SCHEMA_VERSION = "1.0.0"
UNAVAILABLE = "unavailable"
NOT_APPLICABLE = "not_applicable"
CANONICAL_UNITS = frozenset(
    {
        "input_token",
        "cached_input_token",
        "cache_write_token",
        "output_token",
        "reasoning_token",
        "ai_credit",
        "tool_call",
        "request",
        "usd",
        "cpu_second",
        "byte_second",
        "disk_byte",
        "disk_byte_second",
        "wall_second",
        "provider_latency_second",
        "quota_rejection",
        "throttle_event",
        "reset_event",
        "subscription_allowance",
        "billing_period",
        "storage_read_operation",
        "storage_write_operation",
        "network_operation",
        "gigabyte_month",
    }
)
MEASUREMENT_STATUSES = frozenset(
    {"metered", "estimated", "subscription_normalized", "invoice_reconciled", UNAVAILABLE}
)
_SOURCE_AUTHORITIES = frozenset(
    {"native_provider", "native_local_counter", "native_control_clock", "native_invoice"}
)
_SAFE_REF = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_LEDGER_UNITS = MappingProxyType(
    {
        "copilot_subscription": frozenset(
            {
                "input_token",
                "cached_input_token",
                "cache_write_token",
                "output_token",
                "reasoning_token",
                "ai_credit",
                "tool_call",
                "request",
                "provider_latency_second",
                "quota_rejection",
                "throttle_event",
                "reset_event",
                "subscription_allowance",
                "billing_period",
            }
        ),
        "framework_openai": frozenset(
            {
                "input_token",
                "cached_input_token",
                "cache_write_token",
                "output_token",
                "reasoning_token",
                "tool_call",
                "request",
                "provider_latency_second",
                "usd",
            }
        ),
        "local_resources": frozenset(
            {
                "cpu_second",
                "byte_second",
                "disk_byte",
                "disk_byte_second",
                "wall_second",
                "storage_read_operation",
                "storage_write_operation",
                "network_operation",
                "gigabyte_month",
            }
        ),
    }
)
_NATIVE_FIELD_UNITS = MappingProxyType(
    {
        "copilot_subscription": MappingProxyType(
            {
                "input_tokens": "input_token",
                "cached_input_tokens": "cached_input_token",
                "cache_write_tokens": "cache_write_token",
                "output_tokens": "output_token",
                "reasoning_tokens": "reasoning_token",
                "ai_credits": "ai_credit",
                "tool_calls": "tool_call",
                "requests": "request",
                "provider_latency_seconds": "provider_latency_second",
                "quota_rejections": "quota_rejection",
                "throttles": "throttle_event",
                "resets": "reset_event",
                "allowance": "subscription_allowance",
                "billing_periods": "billing_period",
            }
        ),
        "framework_openai": MappingProxyType(
            {
                "input_tokens": "input_token",
                "cached_input_tokens": "cached_input_token",
                "cache_write_tokens": "cache_write_token",
                "output_tokens": "output_token",
                "reasoning_tokens": "reasoning_token",
                "tool_calls": "tool_call",
                "requests": "request",
                "provider_latency_seconds": "provider_latency_second",
                "api_cost_usd": "usd",
            }
        ),
        "local_resources": MappingProxyType(
            {
                "cpu_seconds": "cpu_second",
                "memory_byte_seconds": "byte_second",
                "disk_bytes": "disk_byte",
                "disk_byte_seconds": "disk_byte_second",
                "wall_seconds": "wall_second",
                "storage_reads": "storage_read_operation",
                "storage_writes": "storage_write_operation",
                "network_operations": "network_operation",
                "gigabyte_months": "gigabyte_month",
            }
        ),
    }
)
_CREDENTIAL_VARIABLE_NAMES = frozenset(
    {"OPENAI_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "COPILOT_AUTH_TOKEN", "COPILOT_GITHUB_TOKEN"}
)


@dataclass(frozen=True, slots=True)
class IdentityEvidence:
    """Secret-safe identity projection for telemetry, manifest, or cost evidence."""

    source_kind: str
    source_ref: str
    identity: ProviderIdentity
    findings: tuple[SecretScanFinding, ...] = ()

    def __post_init__(self) -> None:
        if self.source_kind not in {"telemetry", "manifest", "cost"} or not _SAFE_REF.fullmatch(
            self.source_ref
        ):
            raise AuthorityConflictError(
                "authority_conflict", ("invalid_identity_evidence_source",)
            )
        object.__setattr__(self, "findings", tuple(self.findings))
        if self.findings:
            raise SecretBoundaryViolationError(self.findings)

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": PROVIDER_IDENTITY_SCHEMA_VERSION,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "identity": self.identity.to_record(),
        }


@dataclass(frozen=True, slots=True)
class CostRecord:
    """One immutable, raw quantity observation; never a mutable monetary view."""

    cost_entry_id: CostEntryId
    attempt_id: AttemptId
    identity: ProviderIdentity
    source_authority: str
    source_ref: str
    source_sha256: str
    quantity: int | str
    unit: str
    measurement_status: str
    observed_at: datetime
    price_table_version: str = NOT_APPLICABLE
    price_table_ref: str = NOT_APPLICABLE
    currency: str = NOT_APPLICABLE
    conversion_table_version: str = NOT_APPLICABLE
    conversion_table_sha256: str = NOT_APPLICABLE
    instrumentation_active: bool = False
    lineage_entry_ids: tuple[CostEntryId, ...] = ()
    schema_version: str = COST_LEDGER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "lineage_entry_ids", tuple(self.lineage_entry_ids))
        if (
            self.schema_version != COST_LEDGER_SCHEMA_VERSION
            or not isinstance(self.cost_entry_id, CostEntryId)
            or not isinstance(self.attempt_id, AttemptId)
            or self.source_authority not in _SOURCE_AUTHORITIES
            or not _SAFE_REF.fullmatch(self.source_ref)
            or not _SHA256.fullmatch(self.source_sha256)
            or self.unit not in CANONICAL_UNITS
            or self.measurement_status not in MEASUREMENT_STATUSES
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() != UTC.utcoffset(None)
        ):
            raise AuthorityConflictError("authority_conflict", ("invalid_cost_record",))
        if self.unit not in _LEDGER_UNITS[self.identity.logical_ledger]:
            raise AuthorityConflictError("authority_conflict", ("cost_unit_ledger_mismatch",))
        if (
            self.identity.logical_ledger == "copilot_subscription"
            and self.source_authority != "native_provider"
        ):
            raise AuthorityConflictError("authority_conflict", ("cost_source_authority_mismatch",))
        if self.identity.logical_ledger == "framework_openai" and self.source_authority not in {
            "native_provider",
            "native_invoice",
        }:
            raise AuthorityConflictError("authority_conflict", ("cost_source_authority_mismatch",))
        if self.identity.logical_ledger == "local_resources" and self.source_authority not in {
            "native_local_counter",
            "native_control_clock",
        }:
            raise AuthorityConflictError("authority_conflict", ("cost_source_authority_mismatch",))
        if self.quantity == UNAVAILABLE:
            if self.measurement_status != UNAVAILABLE or self.instrumentation_active:
                raise AuthorityConflictError(
                    "authority_conflict", ("invalid_unavailable_quantity",)
                )
        elif (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity < 0
            or self.measurement_status == UNAVAILABLE
            or (self.quantity == 0 and not self.instrumentation_active)
        ):
            raise AuthorityConflictError("authority_conflict", ("invalid_cost_quantity",))
        if self.unit == "usd":
            if self.quantity == UNAVAILABLE and any(
                value != NOT_APPLICABLE
                for value in (self.currency, self.price_table_version, self.price_table_ref)
            ):
                raise AuthorityConflictError("authority_conflict", ("unexpected_price_authority",))
            if self.quantity != UNAVAILABLE and (
                not re.fullmatch(r"[A-Z]{3}", self.currency)
                or self.price_table_version == NOT_APPLICABLE
                or self.price_table_ref == NOT_APPLICABLE
            ):
                raise AuthorityConflictError(
                    "authority_conflict", ("invalid_currency_price_authority",)
                )
        elif any(
            value != NOT_APPLICABLE
            for value in (self.currency, self.price_table_version, self.price_table_ref)
        ):
            raise AuthorityConflictError("authority_conflict", ("unexpected_price_authority",))
        for value in (
            self.price_table_version,
            self.price_table_ref,
            self.conversion_table_version,
        ):
            if value != NOT_APPLICABLE and not _SAFE_REF.fullmatch(value):
                raise AuthorityConflictError("authority_conflict", ("invalid_versioned_authority",))
        if self.conversion_table_sha256 != NOT_APPLICABLE and not _SHA256.fullmatch(
            self.conversion_table_sha256
        ):
            raise AuthorityConflictError("authority_conflict", ("invalid_conversion_authority",))
        if any(not isinstance(entry_id, CostEntryId) for entry_id in self.lineage_entry_ids):
            raise AuthorityConflictError("authority_conflict", ("invalid_cost_lineage",))
        _require_safe_identity_boundary(self.to_record())

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "cost_entry_id": str(self.cost_entry_id),
            "attempt_id": str(self.attempt_id),
            "identity": self.identity.to_record(),
            "source_authority": self.source_authority,
            "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "quantity": self.quantity,
            "unit": self.unit,
            "measurement_status": self.measurement_status,
            "observed_at": self.observed_at.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            ),
            "price_table_version": self.price_table_version,
            "price_table_ref": self.price_table_ref,
            "currency": self.currency,
            "conversion_table_version": self.conversion_table_version,
            "conversion_table_sha256": self.conversion_table_sha256,
            "instrumentation_active": self.instrumentation_active,
            "lineage_entry_ids": [str(entry_id) for entry_id in self.lineage_entry_ids],
        }


@dataclass(frozen=True, slots=True)
class UnitConversion:
    """One explicit, integer-preserving conversion permitted by a frozen table."""

    source_unit: str
    target_unit: str
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if (
            self.source_unit not in CANONICAL_UNITS
            or self.target_unit not in CANONICAL_UNITS
            or self.source_unit == self.target_unit
            or not isinstance(self.numerator, int)
            or not isinstance(self.denominator, int)
            or self.numerator <= 0
            or self.denominator <= 0
        ):
            raise AuthorityConflictError("authority_conflict", ("invalid_unit_conversion",))


@dataclass(frozen=True, slots=True)
class UnitConversionTable:
    """Hash-pinned conversion authority; absent tables authorize no conversion."""

    version: str
    sha256: str
    conversions: tuple[UnitConversion, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "conversions", tuple(self.conversions))
        if not _SAFE_REF.fullmatch(self.version) or not _SHA256.fullmatch(self.sha256):
            raise AuthorityConflictError("authority_conflict", ("invalid_conversion_table",))
        pairs = [(item.source_unit, item.target_unit) for item in self.conversions]
        if len(pairs) != len(set(pairs)):
            raise AuthorityConflictError("authority_conflict", ("duplicate_unit_conversion",))

    def convert(self, quantity: int, source_unit: str, target_unit: str) -> int:
        if source_unit == target_unit:
            return quantity
        conversion = next(
            (
                item
                for item in self.conversions
                if item.source_unit == source_unit and item.target_unit == target_unit
            ),
            None,
        )
        if conversion is None or quantity * conversion.numerator % conversion.denominator:
            raise AuthorityConflictError("authority_conflict", ("incompatible_units",))
        return quantity * conversion.numerator // conversion.denominator


@dataclass(frozen=True, slots=True)
class QuantityTotal:
    """An aggregation result that retains its homogeneous authority tuple."""

    quantity: int
    unit: str
    identity: ProviderIdentity
    conversion_table_version: str = NOT_APPLICABLE


def aggregate_quantities(
    records: Iterable[CostRecord],
    *,
    target_unit: str,
    conversion_table: UnitConversionTable | None = None,
) -> QuantityTotal:
    """Aggregate only homogeneous raw quantities using an exact frozen conversion."""

    entries = tuple(records)
    if not entries or target_unit not in CANONICAL_UNITS:
        raise AuthorityConflictError("authority_conflict", ("invalid_quantity_aggregation",))
    identity = entries[0].identity
    if any(entry.quantity == UNAVAILABLE for entry in entries):
        raise AuthorityConflictError(
            "authority_conflict", ("unavailable_quantity_cannot_aggregate",)
        )
    if any(
        entry.identity.provider != identity.provider
        or entry.identity.credential_domain != identity.credential_domain
        or entry.identity.cost_source != identity.cost_source
        or entry.identity.logical_ledger != identity.logical_ledger
        for entry in entries
    ):
        raise AuthorityConflictError(
            "authority_conflict", ("cross_authority_quantity_aggregation",)
        )
    if target_unit not in _LEDGER_UNITS[identity.logical_ledger]:
        raise AuthorityConflictError("authority_conflict", ("cost_unit_ledger_mismatch",))
    if any(entry.unit != target_unit for entry in entries) and conversion_table is None:
        raise AuthorityConflictError("authority_conflict", ("conversion_table_required",))
    total = 0
    for entry in entries:
        quantity = entry.quantity
        assert isinstance(quantity, int)
        if entry.unit == target_unit:
            total += quantity
        else:
            assert conversion_table is not None
            if (
                entry.conversion_table_version != conversion_table.version
                or entry.conversion_table_sha256 != conversion_table.sha256
            ):
                raise AuthorityConflictError(
                    "authority_conflict", ("conversion_authority_mismatch",)
                )
            total += conversion_table.convert(quantity, entry.unit, target_unit)
    return QuantityTotal(
        total,
        target_unit,
        identity,
        conversion_table.version if conversion_table is not None else NOT_APPLICABLE,
    )


def validate_cost_records(records: Iterable[CostRecord]) -> tuple[CostRecord, ...]:
    """Reject conflicting duplicate claims while retaining independently sourced observations."""

    by_source_field: dict[tuple[str, str, str], CostRecord] = {}
    for record in records:
        key = (record.source_authority, record.source_ref, record.unit)
        previous = by_source_field.get(key)
        if previous is not None and previous != record:
            raise AuthorityConflictError(
                "authority_conflict",
                ("conflicting_duplicate_quantity_evidence",),
            )
        by_source_field[key] = record
    return tuple(by_source_field.values())


def normalize_native_quantities(
    *,
    attempt_id: AttemptId,
    identity: ProviderIdentity,
    source_authority: str,
    source_ref: str,
    source_sha256: str,
    observed_at: datetime,
    raw_quantities: Mapping[str, int],
    instrumentation_active: bool,
    measurement_status: str = "metered",
    price_table_version: str = NOT_APPLICABLE,
    price_table_ref: str = NOT_APPLICABLE,
    currency: str = NOT_APPLICABLE,
) -> tuple[CostRecord, ...]:
    """Project exact native fields and emit explicit unavailable entries for every absence."""

    field_units = _NATIVE_FIELD_UNITS[identity.logical_ledger]
    if set(raw_quantities).difference(field_units):
        raise AuthorityConflictError("authority_conflict", ("unknown_native_quantity_field",))
    records: list[CostRecord] = []
    for field, unit in field_units.items():
        quantity = raw_quantities.get(field, UNAVAILABLE)
        records.append(
            CostRecord(
                CostEntryId.new(),
                attempt_id,
                identity,
                source_authority,
                source_ref,
                source_sha256,
                quantity,
                unit,
                measurement_status if quantity != UNAVAILABLE else UNAVAILABLE,
                observed_at,
                price_table_version=price_table_version if unit == "usd" else NOT_APPLICABLE,
                price_table_ref=price_table_ref if unit == "usd" else NOT_APPLICABLE,
                currency=currency if unit == "usd" else NOT_APPLICABLE,
                instrumentation_active=instrumentation_active if quantity != UNAVAILABLE else False,
            )
        )
    return tuple(records)


def publish_cost_record(
    record: CostRecord,
    *,
    artifact_store: ArtifactStorePort,
    ledger: LedgerPort,
    run_id: RunId,
    source_evidence: ArtifactRef,
) -> ArtifactRef:
    """Write immutable cost evidence first, then append only its typed ledger reference."""

    artifact = artifact_store.put_bytes(
        cost_record_bytes(record),
        media_type="application/json",
        classification="cost_quantity_evidence",
    )
    result = ledger.submit_intent(
        CostLedgerIntent(
            IntentMetadata(
                IntentId.new(),
                record.observed_at,
                source_attempt_id=record.attempt_id,
                evidence_refs=(artifact, source_evidence),
                reason_code="cost_quantity_recorded",
            ),
            record.cost_entry_id,
            run_id,
            record.attempt_id,
            record.identity.logical_ledger,
            artifact,
            source_evidence,
        )
    )
    if getattr(result, "reason_code", None) is not None:
        raise AuthorityConflictError("authority_conflict", ("cost_ledger_intent_rejected",))
    return artifact


def publish_native_quantity_ledger(
    *,
    attempt_id: AttemptId,
    run_id: RunId,
    identity: ProviderIdentity,
    source_authority: str,
    source_ref: str,
    source_evidence: ArtifactRef,
    raw_quantities: Mapping[str, int],
    instrumentation_active: bool,
    artifact_store: ArtifactStorePort,
    ledger: LedgerPort,
    observed_at: datetime,
) -> tuple[ArtifactRef, ...]:
    """Publish every exposed or unavailable field without altering its native authority."""

    records = normalize_native_quantities(
        attempt_id=attempt_id,
        identity=identity,
        source_authority=source_authority,
        source_ref=source_ref,
        source_sha256=source_evidence.sha256,
        observed_at=observed_at,
        raw_quantities=raw_quantities,
        instrumentation_active=instrumentation_active,
    )
    return tuple(
        publish_cost_record(
            record,
            artifact_store=artifact_store,
            ledger=ledger,
            run_id=run_id,
            source_evidence=source_evidence,
        )
        for record in records
    )


def validate_identity_evidence(
    records: tuple[IdentityEvidence, ...],
) -> tuple[IdentityEvidence, ...]:
    """Preserve each source claim and fail if a source ref changes authority."""

    by_source: dict[tuple[str, str], IdentityEvidence] = {}
    for record in records:
        _require_safe_identity_boundary(record.to_record())
        key = (record.source_kind, record.source_ref)
        previous = by_source.get(key)
        if previous is not None and previous.identity != record.identity:
            raise AuthorityConflictError(
                "authority_conflict",
                ("telemetry_identity_disagreement", record.source_kind),
            )
        by_source[key] = record
    return tuple(by_source.values())


def cost_record_bytes(record: CostRecord) -> bytes:
    """Return canonical value-safe quantity evidence bytes for CAS persistence."""

    _require_safe_identity_boundary(record.to_record())
    return canonical_bytes(record.to_record())


def identity_evidence_bytes(record: IdentityEvidence | CostRecord) -> bytes:
    """Backward-compatible canonical evidence serialization."""

    return (
        cost_record_bytes(record)
        if isinstance(record, CostRecord)
        else canonical_bytes(record.to_record())
    )


def append_authority_conflict(
    ledger: LedgerPort,
    *,
    run_id: RunId,
    attempt_id: AttemptId,
    source_refs: tuple[ArtifactRef, ...],
    conflict_fields: tuple[str, ...],
) -> object:
    """Append an immutable, typed ineligibility fact through the control ledger only."""

    if not source_refs or not conflict_fields:
        raise AuthorityConflictError("authority_conflict", ("missing_conflict_evidence",))
    intent = AuthorityConflictIntent(
        IntentMetadata(
            IntentId.new(),
            datetime.now(UTC),
            source_attempt_id=attempt_id,
            evidence_refs=source_refs,
            reason_code="authority_conflict",
        ),
        run_id,
        attempt_id,
        conflict_fields,
    )
    return ledger.submit_intent(intent)


def environment_identity_projection(
    environment: dict[str, str], identity: ProviderIdentity
) -> dict[str, object]:
    """Project only variable names and identity metadata; credential values never leave a child."""

    findings = scan_secret_boundaries(
        {
            "process_environment_names": tuple(environment),
            "noncredential_environment_values": {
                name: value
                for name, value in environment.items()
                if name not in _CREDENTIAL_VARIABLE_NAMES
            },
        }
    )
    if findings:
        raise SecretBoundaryViolationError(findings)
    return MappingProxyType(
        {
            "schema_version": PROVIDER_IDENTITY_SCHEMA_VERSION,
            "identity": identity.to_record(),
            "environment_names": tuple(sorted(environment)),
        }
    )


def _require_safe_identity_boundary(value: object) -> None:
    findings = scan_secret_boundaries({"provider_identity_evidence": value})
    if findings:
        raise SecretBoundaryViolationError(findings)
