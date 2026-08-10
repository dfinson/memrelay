"""Versioned identity-bearing cost and evidence contracts without price arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import AuthorityConflictError, SecretBoundaryViolationError
from memrelay_eval.domain.identity import PROVIDER_IDENTITY_SCHEMA_VERSION, ProviderIdentity
from memrelay_eval.domain.ids import AttemptId, IntentId, RunId
from memrelay_eval.domain.intents import AuthorityConflictIntent, IntentMetadata
from memrelay_eval.domain.ports import LedgerPort
from memrelay_eval.evidence.secret_scan import SecretScanFinding, scan_secret_boundaries

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
        if self.source_kind not in {"telemetry", "manifest", "cost"} or not self.source_ref:
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
    """Separate logical-ledger input; quantities and pricing remain future-story work."""

    cost_entry_id: str
    attempt_id: str
    identity: ProviderIdentity
    source_ref: str
    quantity: int | str = "unavailable"
    unit: str = "unavailable"
    schema_version: str = PROVIDER_IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            self.schema_version != PROVIDER_IDENTITY_SCHEMA_VERSION
            or not self.cost_entry_id
            or not self.attempt_id
        ):
            raise AuthorityConflictError("authority_conflict", ("invalid_cost_identity_record",))
        if self.quantity != "unavailable" and (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity < 0
        ):
            raise AuthorityConflictError("authority_conflict", ("invalid_cost_quantity",))
        _require_safe_identity_boundary(self.to_record())

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "cost_entry_id": self.cost_entry_id,
            "attempt_id": self.attempt_id,
            "source_ref": self.source_ref,
            "quantity": self.quantity,
            "unit": self.unit,
            "identity": self.identity.to_record(),
        }


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


def identity_evidence_bytes(record: IdentityEvidence | CostRecord) -> bytes:
    """Return canonical value-safe evidence bytes for CAS persistence."""
    _require_safe_identity_boundary(record.to_record())
    return canonical_bytes(record.to_record())


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
