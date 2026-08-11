"""Standard-library-only observation sentinel qualification contract."""

from __future__ import annotations

import re
import secrets
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from .errors import ObservationQualificationError

OBSERVATION_QUALIFICATION_SCHEMA_VERSION = "1.0.0"
OBSERVATION_PROTOCOL_FAMILY = "memrelay.eval.observation-sentinel"
OBSERVATION_ESTIMAND = (
    "The path-specific expected-sentinel delivery proportion over the frozen conformance "
    "window, together with ordering, pre/post-idempotency duplicate, gap, restart-recovery, "
    "deadline-delay, and terminal-flush behavior."
)
OBSERVATION_NON_CLAIMS = (
    "This observation-only estimand is not coding efficacy, retrieval quality, safety, "
    "economic value, a causal treatment effect, production-wide reliability, or "
    "cross-repository fitness."
)

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SENTINEL_ID = re.compile(r"^sentinel_[a-f0-9]{32}$")
_PROTOCOL_VERSION = re.compile(r"^memrelay\.eval\.observation-sentinel/1\.0\.0\+[a-f0-9]{16}$")


class ObservationPath(StrEnum):
    """Configured product compositions; polling is shared, not a third source."""

    REPLAY = "replay"
    FILE_WATCH = "file_watch"


class ObservationBoundary(StrEnum):
    """Native and evaluator boundaries that must retain each sentinel."""

    DISCOVERY = "discovery"
    CAPTURE = "capture"
    PRE_IDEMPOTENCY = "pre_idempotency"
    SPOOL = "spool"
    DAEMON = "daemon"
    MCP_GRAPH = "mcp_graph"
    TELEMETRY = "telemetry"
    MANIFEST = "manifest"
    TERMINAL_FLUSH = "terminal_flush"
    RECONCILIATION = "reconciliation"


class ObservationFailureReason(StrEnum):
    """Stable fail-closed reasons emitted by one path qualification decision."""

    PATH_MISMATCH = "observation_path_mismatch"
    CONFORMANCE_IDENTITY_DRIFT = "observation_conformance_identity_drift"
    MISSING = "observation_sentinel_missing"
    DUPLICATED = "observation_sentinel_duplicated"
    REORDERED = "observation_sentinel_reordered"
    DELAYED = "observation_sentinel_delayed"
    GAP = "observation_sentinel_gap"
    RESTART_GAP = "observation_restart_gap"
    TERMINAL_FLUSH_MISSING = "observation_terminal_flush_missing"
    UNRECONCILED = "observation_sentinel_unreconciled"


_REQUIRED_BOUNDARIES = frozenset(ObservationBoundary)


def observation_protocol_version(conformance_sha256: str) -> str:
    """Derive a protocol version that cannot be reused after identity drift."""

    if not _SHA256.fullmatch(conformance_sha256):
        raise ValueError("observation_conformance_hash_invalid")
    return f"{OBSERVATION_PROTOCOL_FAMILY}/1.0.0+{conformance_sha256[:16]}"


def observation_contract_from_document(value: Mapping[str, object]) -> ObservationContract:
    """Parse one exact, value-safe observation contract from canonical JSON."""

    _require_document_keys(
        value,
        {
            "schema_version",
            "path",
            "identity",
            "expected_sentinels",
            "window_started_at",
            "deadline_at",
            "require_restart_recovery",
            "estimand",
            "non_claims",
        },
        "contract",
    )
    identity_value = _document_mapping(value["identity"], "identity")
    _require_document_keys(
        identity_value,
        {
            "source_implementation_sha256",
            "semantic_map_sha256",
            "configuration_sha256",
            "runtime_lock_sha256",
            "sentinel_contract_sha256",
            "reconciliation_policy_sha256",
            "conformance_sha256",
            "protocol_version",
        },
        "identity",
    )
    sentinels: list[ObservationSentinel] = []
    for item in _document_list(value["expected_sentinels"], "expected_sentinels"):
        sentinel = _document_mapping(item, "expected_sentinel")
        _require_document_keys(sentinel, {"identifier", "sequence"}, "expected_sentinel")
        sentinels.append(
            ObservationSentinel(
                _document_string(sentinel["identifier"], "expected_sentinel.identifier"),
                _document_integer(sentinel["sequence"], "expected_sentinel.sequence"),
            )
        )
    if value["estimand"] != OBSERVATION_ESTIMAND or value["non_claims"] != OBSERVATION_NON_CLAIMS:
        raise ValueError("observation_contract_claim_scope_invalid")
    return ObservationContract(
        path=ObservationPath(_document_string(value["path"], "contract.path")),
        identity=ObservationIdentity(
            **{
                key: _document_string(identity_value[key], f"identity.{key}")
                for key in sorted(identity_value)
            }
        ),
        expected_sentinels=tuple(sentinels),
        window_started_at=_document_timestamp(value["window_started_at"], "window_started_at"),
        deadline_at=_document_timestamp(value["deadline_at"], "deadline_at"),
        require_restart_recovery=_document_boolean(
            value["require_restart_recovery"], "require_restart_recovery"
        ),
        schema_version=_document_string(value["schema_version"], "contract.schema_version"),
    )


def observation_evidence_from_document(value: Mapping[str, object]) -> ObservationEvidence:
    """Parse exact retained boundary records without admitting source payloads."""

    _require_document_keys(
        value,
        {
            "path",
            "conformance_sha256",
            "records",
            "final_drain_completed",
            "collector_shutdown_verified",
            "reconciliation_completed",
            "authority_conflict",
            "partial_success",
        },
        "evidence",
    )
    records: list[SentinelBoundaryRecord] = []
    for item in _document_list(value["records"], "records"):
        record = _document_mapping(item, "record")
        _require_document_keys(
            record,
            {"path", "boundary", "sentinel_id", "sequence", "observed_at", "restart_epoch"},
            "record",
        )
        records.append(
            SentinelBoundaryRecord(
                path=ObservationPath(_document_string(record["path"], "record.path")),
                boundary=ObservationBoundary(
                    _document_string(record["boundary"], "record.boundary")
                ),
                sentinel_id=_document_string(record["sentinel_id"], "record.sentinel_id"),
                sequence=_document_integer(record["sequence"], "record.sequence"),
                observed_at=_document_timestamp(record["observed_at"], "record.observed_at"),
                restart_epoch=_document_integer(record["restart_epoch"], "record.restart_epoch"),
            )
        )
    return ObservationEvidence(
        path=ObservationPath(_document_string(value["path"], "evidence.path")),
        conformance_sha256=_document_string(
            value["conformance_sha256"], "evidence.conformance_sha256"
        ),
        records=tuple(records),
        final_drain_completed=_document_boolean(
            value["final_drain_completed"], "final_drain_completed"
        ),
        collector_shutdown_verified=_document_boolean(
            value["collector_shutdown_verified"], "collector_shutdown_verified"
        ),
        reconciliation_completed=_document_boolean(
            value["reconciliation_completed"], "reconciliation_completed"
        ),
        authority_conflict=_document_boolean(value["authority_conflict"], "authority_conflict"),
        partial_success=_document_boolean(value["partial_success"], "partial_success"),
    )


def generate_sentinels(
    count: int, *, token_bytes: Callable[[int], bytes] = secrets.token_bytes
) -> tuple[ObservationSentinel, ...]:
    """Generate opaque, high-entropy synthetic identifiers without source content."""

    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError("observation_sentinel_count_invalid")
    sentinels: list[ObservationSentinel] = []
    identifiers: set[str] = set()
    while len(sentinels) < count:
        token = token_bytes(16)
        if not isinstance(token, bytes) or len(token) != 16:
            raise ValueError("observation_sentinel_entropy_invalid")
        identifier = f"sentinel_{token.hex()}"
        if identifier in identifiers:
            continue
        identifiers.add(identifier)
        sentinels.append(ObservationSentinel(identifier, len(sentinels) + 1))
    return tuple(sentinels)


@dataclass(frozen=True, slots=True)
class ObservationSentinel:
    """One opaque expected event in a frozen conformance sequence."""

    identifier: str
    sequence: int

    def __post_init__(self) -> None:
        if not _SENTINEL_ID.fullmatch(self.identifier):
            raise ValueError("observation_sentinel_identifier_invalid")
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 1
        ):
            raise ValueError("observation_sentinel_sequence_invalid")

    def to_document(self) -> dict[str, object]:
        return {"identifier": self.identifier, "sequence": self.sequence}


@dataclass(frozen=True, slots=True)
class ObservationIdentity:
    """Hashes and protocol identity frozen before sentinel injection."""

    source_implementation_sha256: str
    semantic_map_sha256: str
    configuration_sha256: str
    runtime_lock_sha256: str
    sentinel_contract_sha256: str
    reconciliation_policy_sha256: str
    conformance_sha256: str
    protocol_version: str

    def __post_init__(self) -> None:
        for value in (
            self.source_implementation_sha256,
            self.semantic_map_sha256,
            self.configuration_sha256,
            self.runtime_lock_sha256,
            self.sentinel_contract_sha256,
            self.reconciliation_policy_sha256,
            self.conformance_sha256,
        ):
            if not _SHA256.fullmatch(value):
                raise ValueError("observation_identity_hash_invalid")
        if not _PROTOCOL_VERSION.fullmatch(
            self.protocol_version
        ) or self.protocol_version != observation_protocol_version(self.conformance_sha256):
            raise ValueError("observation_protocol_version_invalid")

    def to_document(self) -> dict[str, object]:
        return {
            "source_implementation_sha256": self.source_implementation_sha256,
            "semantic_map_sha256": self.semantic_map_sha256,
            "configuration_sha256": self.configuration_sha256,
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "sentinel_contract_sha256": self.sentinel_contract_sha256,
            "reconciliation_policy_sha256": self.reconciliation_policy_sha256,
            "conformance_sha256": self.conformance_sha256,
            "protocol_version": self.protocol_version,
        }


@dataclass(frozen=True, slots=True)
class ObservationContract:
    """One path's frozen estimand, denominator, deadline, and implementation identity."""

    path: ObservationPath
    identity: ObservationIdentity
    expected_sentinels: tuple[ObservationSentinel, ...]
    window_started_at: datetime
    deadline_at: datetime
    require_restart_recovery: bool = True
    schema_version: str = OBSERVATION_QUALIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != OBSERVATION_QUALIFICATION_SCHEMA_VERSION:
            raise ValueError("observation_contract_schema_version_invalid")
        if not isinstance(self.path, ObservationPath):
            raise ValueError("observation_contract_path_invalid")
        sentinels = tuple(self.expected_sentinels)
        if not sentinels:
            raise ValueError("observation_expected_sentinels_empty")
        if len({item.identifier for item in sentinels}) != len(sentinels):
            raise ValueError("observation_expected_sentinels_duplicate")
        if [item.sequence for item in sentinels] != list(range(1, len(sentinels) + 1)):
            raise ValueError("observation_expected_sentinels_order_invalid")
        started_at = _utc(self.window_started_at, "window_started_at")
        deadline_at = _utc(self.deadline_at, "deadline_at")
        if deadline_at <= started_at:
            raise ValueError("observation_window_deadline_invalid")
        object.__setattr__(self, "expected_sentinels", sentinels)
        object.__setattr__(self, "window_started_at", started_at)
        object.__setattr__(self, "deadline_at", deadline_at)

    @property
    def expected_identifiers(self) -> tuple[str, ...]:
        return tuple(item.identifier for item in self.expected_sentinels)

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "path": self.path.value,
            "identity": self.identity.to_document(),
            "expected_sentinels": [item.to_document() for item in self.expected_sentinels],
            "window_started_at": _timestamp(self.window_started_at),
            "deadline_at": _timestamp(self.deadline_at),
            "require_restart_recovery": self.require_restart_recovery,
            "estimand": OBSERVATION_ESTIMAND,
            "non_claims": OBSERVATION_NON_CLAIMS,
        }


@dataclass(frozen=True, slots=True)
class SentinelBoundaryRecord:
    """A value-safe native or evaluator observation of one expected sentinel."""

    path: ObservationPath
    boundary: ObservationBoundary
    sentinel_id: str
    sequence: int
    observed_at: datetime
    restart_epoch: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.path, ObservationPath) or not isinstance(
            self.boundary, ObservationBoundary
        ):
            raise ValueError("observation_boundary_invalid")
        if not _SENTINEL_ID.fullmatch(self.sentinel_id):
            raise ValueError("observation_record_sentinel_invalid")
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 1
        ):
            raise ValueError("observation_record_sequence_invalid")
        if (
            not isinstance(self.restart_epoch, int)
            or isinstance(self.restart_epoch, bool)
            or self.restart_epoch < 0
        ):
            raise ValueError("observation_restart_epoch_invalid")
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))

    def to_document(self) -> dict[str, object]:
        return {
            "path": self.path.value,
            "boundary": self.boundary.value,
            "sentinel_id": self.sentinel_id,
            "sequence": self.sequence,
            "observed_at": _timestamp(self.observed_at),
            "restart_epoch": self.restart_epoch,
        }


@dataclass(frozen=True, slots=True)
class ObservationEvidence:
    """Retained boundary records; no source payload, user, or repository data."""

    path: ObservationPath
    conformance_sha256: str
    records: tuple[SentinelBoundaryRecord, ...]
    final_drain_completed: bool
    collector_shutdown_verified: bool
    reconciliation_completed: bool
    authority_conflict: bool = False
    partial_success: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.path, ObservationPath) or not _SHA256.fullmatch(
            self.conformance_sha256
        ):
            raise ValueError("observation_evidence_identity_invalid")
        records = tuple(self.records)
        if not records:
            raise ValueError("observation_evidence_records_empty")
        if any(not isinstance(record, SentinelBoundaryRecord) for record in records):
            raise ValueError("observation_evidence_record_invalid")
        object.__setattr__(self, "records", records)

    def to_document(self) -> dict[str, object]:
        return {
            "path": self.path.value,
            "conformance_sha256": self.conformance_sha256,
            "records": [record.to_document() for record in self.records],
            "final_drain_completed": self.final_drain_completed,
            "collector_shutdown_verified": self.collector_shutdown_verified,
            "reconciliation_completed": self.reconciliation_completed,
            "authority_conflict": self.authority_conflict,
            "partial_success": self.partial_success,
        }


@dataclass(frozen=True, slots=True)
class ObservationAssessment:
    """Immutable per-path qualification outcome; incomplete paths emit no claim."""

    contract: ObservationContract
    evidence: ObservationEvidence
    failure_reasons: tuple[ObservationFailureReason, ...]
    missing_by_boundary: Mapping[ObservationBoundary, tuple[str, ...]]
    pre_idempotency_duplicates: tuple[str, ...]
    post_idempotency_duplicates: tuple[str, ...]
    reordered_boundaries: tuple[ObservationBoundary, ...]
    delayed_sentinels: tuple[str, ...]
    restart_epochs: tuple[int, ...]
    delivery_numerator: int
    delivery_denominator: int

    def __post_init__(self) -> None:
        reasons = tuple(sorted(set(self.failure_reasons), key=lambda item: item.value))
        if any(not isinstance(reason, ObservationFailureReason) for reason in reasons):
            raise ValueError("observation_failure_reason_invalid")
        missing = {
            boundary: tuple(identifiers)
            for boundary, identifiers in self.missing_by_boundary.items()
        }
        if any(
            not isinstance(boundary, ObservationBoundary)
            or any(not _SENTINEL_ID.fullmatch(identifier) for identifier in identifiers)
            for boundary, identifiers in missing.items()
        ):
            raise ValueError("observation_missing_inventory_invalid")
        if self.delivery_denominator != len(self.contract.expected_sentinels):
            raise ValueError("observation_delivery_denominator_not_frozen")
        if not 0 <= self.delivery_numerator <= self.delivery_denominator:
            raise ValueError("observation_delivery_numerator_invalid")
        object.__setattr__(self, "failure_reasons", reasons)
        object.__setattr__(self, "missing_by_boundary", MappingProxyType(missing))
        object.__setattr__(
            self, "pre_idempotency_duplicates", tuple(sorted(set(self.pre_idempotency_duplicates)))
        )
        object.__setattr__(
            self,
            "post_idempotency_duplicates",
            tuple(sorted(set(self.post_idempotency_duplicates))),
        )
        object.__setattr__(
            self,
            "reordered_boundaries",
            tuple(sorted(set(self.reordered_boundaries), key=lambda item: item.value)),
        )
        object.__setattr__(self, "delayed_sentinels", tuple(sorted(set(self.delayed_sentinels))))
        object.__setattr__(self, "restart_epochs", tuple(sorted(set(self.restart_epochs))))

    @property
    def qualified(self) -> bool:
        return not self.failure_reasons

    @property
    def delivery_proportion(self) -> float:
        return self.delivery_numerator / self.delivery_denominator

    @property
    def reason_code(self) -> str:
        return "observation_qualified" if self.qualified else self.failure_reasons[0].value

    def completeness_claim(self) -> str:
        """Return the only supported bounded statement after a fully qualified decision."""

        if not self.qualified:
            raise ObservationQualificationError(self.reason_code)
        return (
            f"The named configured {self.contract.path.value} observation path, its frozen "
            "source/mapping/configuration identity, and its conformance window passed the "
            "sentinel and reconciliation contract."
        )

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": OBSERVATION_QUALIFICATION_SCHEMA_VERSION,
            "artifact_type": "observation_qualification",
            "contract": self.contract.to_document(),
            "evidence": self.evidence.to_document(),
            "qualified": self.qualified,
            "reason_code": self.reason_code,
            "failure_reasons": [item.value for item in self.failure_reasons],
            "delivery_numerator": self.delivery_numerator,
            "delivery_denominator": self.delivery_denominator,
            "delivery_proportion": self.delivery_proportion,
            "missing_by_boundary": [
                {
                    "boundary": boundary.value,
                    "sentinel_ids": list(identifiers),
                }
                for boundary, identifiers in sorted(
                    self.missing_by_boundary.items(), key=lambda item: item[0].value
                )
            ],
            "pre_idempotency_duplicates": list(self.pre_idempotency_duplicates),
            "post_idempotency_duplicates": list(self.post_idempotency_duplicates),
            "reordered_boundaries": [item.value for item in self.reordered_boundaries],
            "delayed_sentinels": list(self.delayed_sentinels),
            "restart_epochs": list(self.restart_epochs),
            "estimand": OBSERVATION_ESTIMAND,
            "non_claims": OBSERVATION_NON_CLAIMS,
        }


def assess_observation(
    contract: ObservationContract, evidence: ObservationEvidence
) -> ObservationAssessment:
    """Fail closed by comparing every preserved boundary against the frozen sequence."""

    reasons: set[ObservationFailureReason] = set()
    expected_by_id = {item.identifier: item for item in contract.expected_sentinels}
    records_by_boundary: dict[ObservationBoundary, list[SentinelBoundaryRecord]] = defaultdict(list)
    restart_epochs: set[int] = set()
    delayed: set[str] = set()

    if evidence.path is not contract.path:
        reasons.add(ObservationFailureReason.PATH_MISMATCH)
    if evidence.conformance_sha256 != contract.identity.conformance_sha256:
        reasons.add(ObservationFailureReason.CONFORMANCE_IDENTITY_DRIFT)

    for record in evidence.records:
        if record.path is not contract.path:
            reasons.add(ObservationFailureReason.PATH_MISMATCH)
            continue
        expected = expected_by_id.get(record.sentinel_id)
        if expected is None or expected.sequence != record.sequence:
            reasons.add(ObservationFailureReason.UNRECONCILED)
            continue
        records_by_boundary[record.boundary].append(record)
        restart_epochs.add(record.restart_epoch)
        if record.observed_at > contract.deadline_at:
            delayed.add(record.sentinel_id)

    missing_by_boundary: dict[ObservationBoundary, tuple[str, ...]] = {}
    pre_idempotency_duplicates: set[str] = set()
    post_idempotency_duplicates: set[str] = set()
    reordered_boundaries: set[ObservationBoundary] = set()
    expected_order = contract.expected_identifiers

    for boundary in _REQUIRED_BOUNDARIES:
        records = records_by_boundary[boundary]
        counts = Counter(record.sentinel_id for record in records)
        missing = tuple(identifier for identifier in expected_order if counts[identifier] == 0)
        if missing:
            missing_by_boundary[boundary] = missing
            reasons.add(ObservationFailureReason.MISSING)
            if any(
                0 < sentinel.sequence - 1 < len(expected_order) - 1
                for sentinel in contract.expected_sentinels
                if sentinel.identifier in missing
            ):
                reasons.add(ObservationFailureReason.GAP)
        duplicates = {identifier for identifier, count in counts.items() if count > 1}
        if boundary is ObservationBoundary.PRE_IDEMPOTENCY:
            pre_idempotency_duplicates.update(duplicates)
        elif duplicates:
            post_idempotency_duplicates.update(duplicates)
            reasons.add(ObservationFailureReason.DUPLICATED)
        observed_order = _first_occurrence_order(records)
        expected_present_order = tuple(
            identifier for identifier in expected_order if identifier in counts
        )
        if observed_order != expected_present_order:
            reordered_boundaries.add(boundary)
            reasons.add(ObservationFailureReason.REORDERED)

    if delayed:
        reasons.add(ObservationFailureReason.DELAYED)
    if contract.require_restart_recovery and not any(epoch > 0 for epoch in restart_epochs):
        reasons.add(ObservationFailureReason.RESTART_GAP)
    terminal_ids = {
        record.sentinel_id for record in records_by_boundary[ObservationBoundary.TERMINAL_FLUSH]
    }
    if (
        not evidence.final_drain_completed
        or contract.expected_sentinels[-1].identifier not in terminal_ids
    ):
        reasons.add(ObservationFailureReason.TERMINAL_FLUSH_MISSING)
    if (
        not evidence.collector_shutdown_verified
        or not evidence.reconciliation_completed
        or evidence.authority_conflict
        or evidence.partial_success
    ):
        reasons.add(ObservationFailureReason.UNRECONCILED)

    reconciliation_ids = {
        record.sentinel_id for record in records_by_boundary[ObservationBoundary.RECONCILIATION]
    }
    return ObservationAssessment(
        contract=contract,
        evidence=evidence,
        failure_reasons=tuple(reasons),
        missing_by_boundary=missing_by_boundary,
        pre_idempotency_duplicates=tuple(pre_idempotency_duplicates),
        post_idempotency_duplicates=tuple(post_idempotency_duplicates),
        reordered_boundaries=tuple(reordered_boundaries),
        delayed_sentinels=tuple(delayed),
        restart_epochs=tuple(restart_epochs),
        delivery_numerator=len(reconciliation_ids),
        delivery_denominator=len(contract.expected_sentinels),
    )


def require_new_protocol(prior: ObservationAssessment, replacement: ObservationContract) -> None:
    """Prevent relabeling prior evidence after any frozen identity input changes."""

    if (
        prior.contract.identity.conformance_sha256 != replacement.identity.conformance_sha256
        and prior.contract.identity.protocol_version == replacement.identity.protocol_version
    ):
        raise ObservationQualificationError("observation_protocol_version_reused_after_drift")


def _first_occurrence_order(records: Sequence[SentinelBoundaryRecord]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for record in records:
        if record.sentinel_id not in seen:
            seen.add(record.sentinel_id)
            ordered.append(record.sentinel_id)
    return tuple(ordered)


def _require_document_keys(value: Mapping[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"observation_{name}_document_keys_invalid")


def _document_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"observation_{name}_document_mapping_invalid")
    return value


def _document_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"observation_{name}_document_list_invalid")
    return value


def _document_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"observation_{name}_document_string_invalid")
    return value


def _document_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"observation_{name}_document_integer_invalid")
    return value


def _document_boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"observation_{name}_document_boolean_invalid")
    return value


def _document_timestamp(value: object, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_document_string(value, name).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"observation_{name}_document_timestamp_invalid") from error
    return _utc(parsed, name)


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"observation_{field}_must_be_utc")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
