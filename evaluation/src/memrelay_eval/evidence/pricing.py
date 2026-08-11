"""Offline, immutable monetary views derived from retained quantity evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import AuthorityConflictError, SecretBoundaryViolationError
from memrelay_eval.domain.ids import IntentId, MonetaryViewId, RunId
from memrelay_eval.domain.intents import IntentMetadata, MonetaryViewIntent
from memrelay_eval.domain.ports import ArtifactStorePort, LedgerPort
from memrelay_eval.evidence.costs import (
    NOT_APPLICABLE,
    UNAVAILABLE,
    CostRecord,
    cost_record_bytes,
)
from memrelay_eval.evidence.secret_scan import scan_secret_boundaries

PRICE_TABLE_SCHEMA_VERSION = "1.0.0"
MONETARY_VIEW_SCHEMA_VERSION = "1.0.0"
DERIVATION_VERSION = "1.0.0"
PER_MILLION = Decimal("1000000")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
_MONETARY_AUTHORITIES = frozenset(
    {
        "estimated",
        "shadow_sensitivity",
        "subscription_normalized",
        "incremental_cash",
        "framework_api_metered",
        "invoice_reconciled",
        "local_variable",
        "fully_loaded",
        "study_cost",
    }
)
_PRICING_STATUSES = frozenset({"derived", "unavailable", "not_applicable"})
MONETARY_CATEGORIES = frozenset(
    {
        "subscription_allowance",
        "subscription_normalized",
        "incremental_cash",
        "framework_api_metered",
        "invoice_reconciled",
        "local_variable",
        "fully_loaded",
        "study_cost",
    }
)


def _utc_z(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise AuthorityConflictError("authority_conflict", ("non_utc_pricing_time",))
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise AuthorityConflictError("authority_conflict", ("invalid_price_table_time",))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AuthorityConflictError("authority_conflict", ("invalid_price_table_time",)) from error
    _utc_z(parsed)
    return parsed


def _decimal(value: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise AuthorityConflictError("authority_conflict", ("invalid_decimal_money_value",))
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise AuthorityConflictError(
            "authority_conflict", ("invalid_decimal_money_value",)
        ) from error
    if not result.is_finite():
        raise AuthorityConflictError("authority_conflict", ("invalid_decimal_money_value",))
    return result


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite() or value < 0:
        raise AuthorityConflictError("authority_conflict", ("invalid_decimal_money_value",))
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


@dataclass(frozen=True, slots=True)
class PriceRate:
    """One exact rate expressed in a table's declared integer scale."""

    unit: str
    rate_per_scale: str

    def __post_init__(self) -> None:
        if self.unit not in {"input_token", "cached_input_token", "output_token"}:
            raise AuthorityConflictError("authority_conflict", ("unsupported_price_unit",))
        _decimal(self.rate_per_scale)

    @property
    def decimal_rate(self) -> Decimal:
        return _decimal(self.rate_per_scale)

    def to_record(self) -> dict[str, str]:
        return {"unit": self.unit, "rate_per_scale": _decimal_text(self.decimal_rate)}


@dataclass(frozen=True, slots=True)
class PriceTable:
    """A dated, hash-pinned table; its bytes are immutable once put into CAS."""

    version: str
    provider: str
    product: str
    model: str
    effective_from: datetime
    effective_to: datetime
    billing_region: str
    currency: str
    scale: int
    tax_discount_credit_treatment: str
    source_authority: str
    source_ref: str
    source_sha256: str
    retrieved_at: datetime
    rates: tuple[PriceRate, ...]
    conversion_table_version: str = NOT_APPLICABLE
    conversion_table_sha256: str = NOT_APPLICABLE
    conversion_table_ref: str = NOT_APPLICABLE
    schema_version: str = PRICE_TABLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "rates", tuple(self.rates))
        if (
            self.schema_version != PRICE_TABLE_SCHEMA_VERSION
            or not all(
                _SAFE_CODE.fullmatch(value)
                for value in (
                    self.version,
                    self.provider,
                    self.product,
                    self.model,
                    self.billing_region,
                    self.tax_discount_credit_treatment,
                    self.source_authority,
                    self.source_ref,
                )
            )
            or self.provider != "openai"
            or self.product != "framework_openai_api"
            or self.currency != "USD"
            or self.scale != 1_000_000
            or self.source_authority not in {"published_price_table", "native_invoice"}
            or not _SHA256.fullmatch(self.source_sha256)
            or self.effective_to <= self.effective_from
            or self.retrieved_at < self.effective_from
            or not self.rates
            or len({rate.unit for rate in self.rates}) != len(self.rates)
        ):
            raise AuthorityConflictError("authority_conflict", ("invalid_price_table",))
        _utc_z(self.effective_from)
        _utc_z(self.effective_to)
        _utc_z(self.retrieved_at)
        if any(
            value != NOT_APPLICABLE
            for value in (
                self.conversion_table_version,
                self.conversion_table_sha256,
                self.conversion_table_ref,
            )
        ):
            raise AuthorityConflictError(
                "authority_conflict", ("unsupported_currency_conversion_authority",)
            )
        _require_secret_safe(self.to_record())

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "version": self.version,
            "provider": self.provider,
            "product": self.product,
            "model": self.model,
            "effective_from": _utc_z(self.effective_from),
            "effective_to": _utc_z(self.effective_to),
            "billing_region": self.billing_region,
            "currency": self.currency,
            "unit": "token",
            "scale": self.scale,
            "tax_discount_credit_treatment": self.tax_discount_credit_treatment,
            "source_authority": self.source_authority,
            "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "retrieved_at": _utc_z(self.retrieved_at),
            "conversion_table_version": self.conversion_table_version,
            "conversion_table_sha256": self.conversion_table_sha256,
            "conversion_table_ref": self.conversion_table_ref,
            "rates": [rate.to_record() for rate in self.rates],
        }

    @property
    def content_sha256(self) -> str:
        return sha256(price_table_bytes(self)).hexdigest()

    def rate_for(self, unit: str) -> PriceRate | None:
        return next((rate for rate in self.rates if rate.unit == unit), None)


@dataclass(frozen=True, slots=True)
class QuantityPricingInput:
    """One retained quantity plus its immutable evidence and frozen context."""

    record: CostRecord
    quantity_artifact: ArtifactRef
    source_evidence: ArtifactRef
    model: str
    billing_region: str
    protocol_sha256: str
    environment_stratum_sha256: str
    scenario_id: str = "frozen_initial"

    def __post_init__(self) -> None:
        if (
            self.record.identity.logical_ledger != "framework_openai"
            or self.record.identity.provider != "openai"
            or self.record.identity.credential_domain != "framework_openai_api"
            or self.record.identity.cost_source != "openai_api_metered"
            or self.record.source_authority != "native_provider"
            or not all(
                _SAFE_CODE.fullmatch(value)
                for value in (self.model, self.billing_region, self.scenario_id)
            )
            or not _SHA256.fullmatch(self.protocol_sha256)
            or not _SHA256.fullmatch(self.environment_stratum_sha256)
            or self.quantity_artifact.sha256 != sha256(cost_record_bytes(self.record)).hexdigest()
            or self.source_evidence.sha256 != self.record.source_sha256
        ):
            raise AuthorityConflictError(
                "authority_conflict", ("invalid_pricing_quantity_context",)
            )


@dataclass(frozen=True, slots=True)
class MonetaryView:
    """One append-only derived amount or explicit unavailable pricing outcome."""

    id: MonetaryViewId
    attempt_id: str
    category: str
    monetary_authority: str
    pricing_status: str
    amount: str | None
    currency: str
    quantity_entry_sha256: str
    quantity_artifact_sha256: str
    quantity_unit: str
    quantity: int | str
    identity: dict[str, str]
    source_authority: str
    source_ref: str
    source_sha256: str
    observed_at: datetime
    model: str
    billing_region: str
    price_table_version: str
    price_table_sha256: str
    price_table_artifact_sha256: str
    conversion_table_version: str
    conversion_table_sha256: str
    derivation_version: str
    protocol_sha256: str
    environment_stratum_sha256: str
    scenario_id: str
    schema_version: str = MONETARY_VIEW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            self.schema_version != MONETARY_VIEW_SCHEMA_VERSION
            or self.category != "framework_api_metered"
            or self.monetary_authority not in _MONETARY_AUTHORITIES
            or self.pricing_status not in _PRICING_STATUSES
            or self.currency != "USD"
            or self.derivation_version != DERIVATION_VERSION
            or not all(
                _SHA256.fullmatch(value)
                for value in (
                    self.quantity_entry_sha256,
                    self.quantity_artifact_sha256,
                    self.source_sha256,
                    self.price_table_sha256,
                    self.price_table_artifact_sha256,
                    self.protocol_sha256,
                    self.environment_stratum_sha256,
                )
            )
            or not all(
                _SAFE_CODE.fullmatch(value)
                for value in (
                    self.source_authority,
                    self.source_ref,
                    self.model,
                    self.billing_region,
                    self.price_table_version,
                    self.scenario_id,
                )
            )
        ):
            raise AuthorityConflictError("authority_conflict", ("invalid_monetary_view",))
        _utc_z(self.observed_at)
        if self.pricing_status == "derived":
            if self.amount is None:
                raise AuthorityConflictError("authority_conflict", ("missing_derived_amount",))
            _decimal(self.amount)
        elif self.amount is not None:
            raise AuthorityConflictError(
                "authority_conflict", ("nonderived_view_must_not_have_amount",)
            )
        _require_secret_safe(self.to_record())

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "monetary_view_id": str(self.id),
            "attempt_id": self.attempt_id,
            "category": self.category,
            "monetary_authority": self.monetary_authority,
            "pricing_status": self.pricing_status,
            "amount": self.amount,
            "currency": self.currency,
            "quantity_entry_sha256": self.quantity_entry_sha256,
            "quantity_artifact_sha256": self.quantity_artifact_sha256,
            "quantity_unit": self.quantity_unit,
            "quantity": self.quantity,
            "identity": dict(self.identity),
            "source_authority": self.source_authority,
            "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "observed_at": _utc_z(self.observed_at),
            "model": self.model,
            "billing_region": self.billing_region,
            "price_table_version": self.price_table_version,
            "price_table_sha256": self.price_table_sha256,
            "price_table_artifact_sha256": self.price_table_artifact_sha256,
            "conversion_table_version": self.conversion_table_version,
            "conversion_table_sha256": self.conversion_table_sha256,
            "derivation_version": self.derivation_version,
            "protocol_sha256": self.protocol_sha256,
            "environment_stratum_sha256": self.environment_stratum_sha256,
            "scenario_id": self.scenario_id,
        }


def price_table_bytes(table: PriceTable) -> bytes:
    """Canonical bytes for a price authority; this is the table's sole identity input."""

    return canonical_bytes(table.to_record())


def load_price_table(data: bytes, *, expected_sha256: str) -> PriceTable:
    """Load a committed table only when exact canonical bytes match its pinned digest."""

    if not _SHA256.fullmatch(expected_sha256) or sha256(data).hexdigest() != expected_sha256:
        raise AuthorityConflictError("authority_conflict", ("price_table_hash_mismatch",))
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthorityConflictError(
            "authority_conflict", ("invalid_price_table_bytes",)
        ) from error
    if not isinstance(value, dict):
        raise AuthorityConflictError("authority_conflict", ("invalid_price_table_bytes",))
    try:
        rates = tuple(
            PriceRate(item["unit"], item["rate_per_scale"]) for item in value.pop("rates")
        )
        if value.pop("unit", None) != "token":
            raise AuthorityConflictError("authority_conflict", ("invalid_price_table_bytes",))
        value["effective_from"] = _parse_utc(value["effective_from"])
        value["effective_to"] = _parse_utc(value["effective_to"])
        value["retrieved_at"] = _parse_utc(value["retrieved_at"])
        table = PriceTable(rates=rates, **value)
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, AuthorityConflictError):
            raise
        raise AuthorityConflictError(
            "authority_conflict", ("invalid_price_table_bytes",)
        ) from error
    if price_table_bytes(table) != data.rstrip(b"\r\n"):
        raise AuthorityConflictError("authority_conflict", ("noncanonical_price_table",))
    return table


def publish_price_table(table: PriceTable, *, artifact_store: ArtifactStorePort) -> ArtifactRef:
    """Write frozen price authority to CAS before any view may refer to it."""

    return artifact_store.put_bytes(
        price_table_bytes(table),
        media_type="application/json",
        classification="price_table_evidence",
    )


def reprice_retained_quantity(
    quantity: QuantityPricingInput,
    *,
    table: PriceTable,
    table_artifact: ArtifactRef,
) -> MonetaryView:
    """Derive exactly one view without changing the quantity, table, or prior view."""

    if table_artifact.sha256 != table.content_sha256:
        raise AuthorityConflictError("authority_conflict", ("price_table_artifact_mismatch",))
    if (
        table.source_authority != "published_price_table"
        or table.currency != "USD"
        or quantity.model != table.model
        or quantity.billing_region != table.billing_region
        or not (table.effective_from <= quantity.record.observed_at < table.effective_to)
    ):
        raise AuthorityConflictError("authority_conflict", ("price_authority_not_effective",))
    if quantity.record.currency != NOT_APPLICABLE:
        raise AuthorityConflictError("authority_conflict", ("quantity_price_authority_rewrite",))
    if any(
        value != NOT_APPLICABLE
        for value in (
            quantity.record.conversion_table_version,
            quantity.record.conversion_table_sha256,
        )
    ):
        raise AuthorityConflictError("authority_conflict", ("non_exact_or_unknown_conversion",))
    status = "derived"
    amount: str | None
    authority = "estimated"
    if quantity.record.quantity == UNAVAILABLE:
        status = "unavailable"
        amount = None
    else:
        rate = table.rate_for(quantity.record.unit)
        if rate is None:
            status = "not_applicable"
            amount = None
        else:
            assert isinstance(quantity.record.quantity, int)
            amount = _decimal_text(
                Decimal(quantity.record.quantity) / Decimal(table.scale) * rate.decimal_rate
            )
    payload = {
        "attempt_id": str(quantity.record.attempt_id),
        "quantity_entry_sha256": sha256(cost_record_bytes(quantity.record)).hexdigest(),
        "price_table_sha256": table.content_sha256,
        "price_table_artifact_sha256": table_artifact.sha256,
        "derivation_version": DERIVATION_VERSION,
        "scenario_id": quantity.scenario_id,
        "amount": amount,
        "pricing_status": status,
    }
    return MonetaryView(
        MonetaryViewId.from_digest(sha256(canonical_bytes(payload)).hexdigest()),
        str(quantity.record.attempt_id),
        "framework_api_metered",
        authority,
        status,
        amount,
        table.currency,
        sha256(cost_record_bytes(quantity.record)).hexdigest(),
        quantity.quantity_artifact.sha256,
        quantity.record.unit,
        quantity.record.quantity,
        quantity.record.identity.to_record(),
        quantity.record.source_authority,
        quantity.record.source_ref,
        quantity.record.source_sha256,
        quantity.record.observed_at,
        quantity.model,
        quantity.billing_region,
        table.version,
        table.content_sha256,
        table_artifact.sha256,
        quantity.record.conversion_table_version,
        quantity.record.conversion_table_sha256,
        DERIVATION_VERSION,
        quantity.protocol_sha256,
        quantity.environment_stratum_sha256,
        quantity.scenario_id,
    )


def validate_price_table_set(tables: Iterable[PriceTable]) -> tuple[PriceTable, ...]:
    """Reject overlapping or conflicting price authority before any selection occurs."""

    values = tuple(tables)
    if not values:
        raise AuthorityConflictError("authority_conflict", ("missing_price_table",))
    for left_index, left in enumerate(values):
        for right in values[left_index + 1 :]:
            if (
                left.provider == right.provider
                and left.product == right.product
                and left.model == right.model
                and left.billing_region == right.billing_region
                and left.currency == right.currency
                and max(left.effective_from, right.effective_from)
                < min(left.effective_to, right.effective_to)
            ):
                raise AuthorityConflictError("authority_conflict", ("overlapping_price_tables",))
    return values


def reprice_retained_quantities(
    quantities: Iterable[QuantityPricingInput],
    *,
    table: PriceTable,
    table_artifact: ArtifactRef,
) -> tuple[MonetaryView, ...]:
    """Reprice independent retained records deterministically; no aggregation is implicit."""

    return tuple(
        reprice_retained_quantity(quantity, table=table, table_artifact=table_artifact)
        for quantity in quantities
    )


def monetary_view_bytes(view: MonetaryView) -> bytes:
    """Canonical immutable bytes for one derived monetary view."""

    return canonical_bytes(view.to_record())


def publish_monetary_view(
    view: MonetaryView,
    *,
    run_id: RunId,
    quantity: QuantityPricingInput,
    table_artifact: ArtifactRef,
    artifact_store: ArtifactStorePort,
    ledger: LedgerPort,
) -> ArtifactRef:
    """Publish a CAS view first, then atomically append its thin control-owned link."""

    if (
        view.attempt_id != str(quantity.record.attempt_id)
        or view.quantity_artifact_sha256 != quantity.quantity_artifact.sha256
        or view.price_table_artifact_sha256 != table_artifact.sha256
    ):
        raise AuthorityConflictError("authority_conflict", ("monetary_view_lineage_mismatch",))
    artifact = artifact_store.put_bytes(
        monetary_view_bytes(view),
        media_type="application/json",
        classification="derived_monetary_view",
    )
    result = ledger.submit_intent(
        MonetaryViewIntent(
            IntentMetadata(
                IntentId.from_digest(sha256(monetary_view_bytes(view)).hexdigest()),
                view.observed_at,
                source_attempt_id=quantity.record.attempt_id,
                evidence_refs=(
                    artifact,
                    table_artifact,
                    quantity.quantity_artifact,
                    quantity.source_evidence,
                ),
                reason_code="monetary_view_derived",
            ),
            view.id,
            run_id,
            quantity.record.attempt_id,
            view.category,
            artifact,
            table_artifact,
            quantity.quantity_artifact,
        )
    )
    if getattr(result, "reason_code", None) is not None:
        raise AuthorityConflictError("authority_conflict", ("monetary_view_intent_rejected",))
    return artifact


def select_monetary_views(
    views: Sequence[MonetaryView],
    *,
    price_table_sha256: str,
    scenario_id: str,
) -> tuple[MonetaryView, ...]:
    """Select only an explicit frozen price/scenario identity; never infer latest."""

    if not _SHA256.fullmatch(price_table_sha256) or not _SAFE_CODE.fullmatch(scenario_id):
        raise AuthorityConflictError("authority_conflict", ("explicit_selection_required",))
    selected = tuple(
        view
        for view in views
        if view.price_table_sha256 == price_table_sha256 and view.scenario_id == scenario_id
    )
    if not selected:
        raise AuthorityConflictError("authority_conflict", ("selected_monetary_view_unavailable",))
    return selected


def _require_secret_safe(value: object) -> None:
    findings = scan_secret_boundaries({"pricing_evidence": value})
    if findings:
        raise SecretBoundaryViolationError(findings)
