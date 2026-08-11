"""Fail-closed, cross-authority terminal evidence reconciliation."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.entities import (
    ArtifactManifest,
    ArtifactRef,
    AttemptTerminal,
    InclusionDecision,
    RetryAuthorization,
)
from memrelay_eval.domain.errors import (
    ArtifactIntegrityError,
    LedgerIntentConflictError,
    ReconciliationError,
    TerminalDecisionConflictError,
)
from memrelay_eval.domain.ids import (
    ArtifactId,
    AttemptId,
    InclusionId,
    IntentId,
    RetentionPolicyId,
    RunId,
)
from memrelay_eval.domain.intents import (
    InclusionDecisionIntent,
    IntentAck,
    IntentMetadata,
    IntentRejection,
    RunTransitionIntent,
)
from memrelay_eval.domain.ports import ArtifactStorePort, LedgerPort
from memrelay_eval.domain.states import (
    ArtifactScope,
    AttemptTerminalKind,
    EvaluationStratum,
    HistoryMode,
    InclusionStatus,
    RunState,
)
from memrelay_eval.evidence.authority import (
    RECONCILIATION_FROZEN_HASH_KEYS,
    RECONCILIATION_IDENTITY_KEYS,
    ReconciliationAuthority,
)
from memrelay_eval.evidence.manifest import parse_manifest
from memrelay_eval.evidence.projection import (
    EvidencePresence,
    EvidenceProjection,
    ReconciliationBlocker,
    load_evidence_projection,
)
from memrelay_eval.evidence.required import (
    EvidenceKind,
    EvidenceMatrixKey,
    RequiredEvidenceMatrix,
    RequiredEvidenceProducerPolicy,
    RequirementMode,
    producer_is_authoritative,
    required_evidence_matrix,
    required_evidence_producer_policy,
)
from memrelay_eval.evidence.secret_scan import SecretBoundaryScanner

RECONCILIATION_SCHEMA_VERSION = "1.0.0"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_REQUIRED_CLAIMS: Mapping[EvidenceKind, frozenset[str]] = MappingProxyType(
    {
        EvidenceKind.ASSIGNMENT: frozenset({"hash.assignment"}),
        EvidenceKind.LIFECYCLE: frozenset({"run_id", "attempt_id", "retry_lineage"}),
        EvidenceKind.INSPECT_JSON: frozenset({"terminal_state"}),
        EvidenceKind.SDK_TERMINAL: frozenset({"terminal_state"}),
        EvidenceKind.CONFIGURATION: frozenset({"hash.configuration"}),
        EvidenceKind.MODEL_LOCK: frozenset({"model_id", "hash.model"}),
        EvidenceKind.PARITY: frozenset(
            {"hash.parity", "environment_fingerprint", "causal_validity_status"}
        ),
        EvidenceKind.WORKSPACE_PATCH: frozenset({"tamper_status"}),
        EvidenceKind.TREATMENT: frozenset({"contamination_status"}),
        EvidenceKind.GRADING: frozenset({"selection_status", "consistency_status"}),
        EvidenceKind.PANEL: frozenset({"consistency_status"}),
        EvidenceKind.CLEANUP: frozenset({"cleanup_status"}),
        EvidenceKind.TRANSITIONS: frozenset({"hash.transitions"}),
        EvidenceKind.REFERENCED_HASHES: frozenset(
            {f"hash.{name}" for name in RECONCILIATION_FROZEN_HASH_KEYS}
        ),
    }
)
_CATEGORICAL_CLAIMS: Mapping[
    EvidenceKind, tuple[tuple[str, frozenset[str], ReconciliationBlocker], ...]
] = MappingProxyType(
    {
        EvidenceKind.WORKSPACE_PATCH: (
            (
                "tamper_status",
                frozenset({"verified", "tampered"}),
                ReconciliationBlocker.TAMPER,
            ),
        ),
        EvidenceKind.TREATMENT: (
            (
                "contamination_status",
                frozenset({"isolated", "contaminated"}),
                ReconciliationBlocker.CONTAMINATION,
            ),
        ),
        EvidenceKind.GRADING: (
            (
                "selection_status",
                frozenset({"canonical", "favorable_substitution"}),
                ReconciliationBlocker.FAVORABLE_SUBSTITUTION,
            ),
            (
                "consistency_status",
                frozenset({"consistent", "conflict"}),
                ReconciliationBlocker.GRADING_CONFLICT,
            ),
        ),
        EvidenceKind.PANEL: (
            (
                "consistency_status",
                frozenset({"consistent", "conflict"}),
                ReconciliationBlocker.GRADING_CONFLICT,
            ),
        ),
        EvidenceKind.PARITY: (
            (
                "causal_validity_status",
                frozenset({"valid", "conflict"}),
                ReconciliationBlocker.CAUSAL_VALIDITY_CONFLICT,
            ),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One preserved native authority record, never a blended source."""

    kind: EvidenceKind
    authority: str
    status: EvidencePresence
    artifact: ArtifactRef | None
    manifest: ArtifactRef | None
    claims: Mapping[str, str]
    unavailable_reason: str | None = None
    declared_blockers: tuple[ReconciliationBlocker, ...] = ()
    projection: ArtifactRef | None = None

    def __post_init__(self) -> None:
        if not self.authority:
            raise ReconciliationError("evidence_authority_missing")
        if not isinstance(self.status, EvidencePresence):
            raise ReconciliationError("evidence_status_invalid")
        if (self.artifact is None) != (self.manifest is None):
            raise ReconciliationError("artifact_manifest_pair_required")
        if self.status in (EvidencePresence.PRESENT, EvidencePresence.UNAVAILABLE) and (
            self.artifact is None or self.manifest is None
        ):
            raise ReconciliationError("durable_evidence_reference_required")
        if self.projection is not None and not isinstance(self.projection, ArtifactRef):
            raise ReconciliationError("evidence_projection_reference_invalid")
        if self.status is EvidencePresence.UNAVAILABLE and not self.unavailable_reason:
            raise ReconciliationError("explicit_unavailable_reason_required")
        if self.status is not EvidencePresence.UNAVAILABLE and self.unavailable_reason is not None:
            raise ReconciliationError("unavailable_reason_without_unavailable_status")
        if any(
            not isinstance(key, str) or not key or not isinstance(value, str) or not value
            for key, value in self.claims.items()
        ):
            raise ReconciliationError("evidence_claims_must_be_nonempty_strings")
        object.__setattr__(self, "claims", MappingProxyType(dict(self.claims)))
        object.__setattr__(
            self, "declared_blockers", tuple(sorted(set(self.declared_blockers), key=str))
        )

    def to_document(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "authority": self.authority,
            "status": self.status.value,
            "artifact": _ref_document(self.artifact),
            "manifest": _ref_document(self.manifest),
            "claims": dict(sorted(self.claims.items())),
            "unavailable_reason": self.unavailable_reason,
            "declared_blockers": [item.value for item in self.declared_blockers],
            "projection": _ref_document(self.projection),
        }

    def to_report_document(self) -> dict[str, object]:
        """Keep report storage value-free even when source evidence contains a secret."""

        return {
            "kind": self.kind.value,
            "authority_sha256": canonical_digest({"authority": self.authority}),
            "status": self.status.value,
            "artifact": _ref_document(self.artifact),
            "manifest": _ref_document(self.manifest),
            "projection": _ref_document(self.projection),
            "claims_sha256": canonical_digest(dict(sorted(self.claims.items()))),
            "declared_blockers": [item.value for item in self.declared_blockers],
        }


@dataclass(frozen=True, slots=True)
class ReconciliationInput:
    """Canonical, prompt-free input required to decide one terminal run."""

    run_id: RunId
    attempt_id: AttemptId
    matrix_key: EvidenceMatrixKey
    identities: Mapping[str, str]
    frozen_hashes: Mapping[str, str]
    started_at: datetime
    terminal_at: datetime
    reconciled_at: datetime
    protocol_sha256: str
    runtime_lock_sha256: str
    retention_policy_id: RetentionPolicyId
    evidence: tuple[EvidenceRecord, ...]
    declared_blockers: tuple[ReconciliationBlocker, ...] = ()
    schema_version: str = RECONCILIATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RECONCILIATION_SCHEMA_VERSION:
            raise ReconciliationError("unsupported_reconciliation_schema_version")
        if self.matrix_key.stage is None:
            raise ReconciliationError("reconciliation_stage_missing")
        if set(self.identities) != RECONCILIATION_IDENTITY_KEYS:
            raise ReconciliationError("terminal_identity_inventory_incomplete")
        if self.identities["run_id"] != str(self.run_id) or self.identities["attempt_id"] != str(
            self.attempt_id
        ):
            raise ReconciliationError("terminal_identity_inventory_conflict")
        if any(not isinstance(value, str) or not value for value in self.identities.values()):
            raise ReconciliationError("terminal_identity_inventory_invalid")
        if set(self.frozen_hashes) != RECONCILIATION_FROZEN_HASH_KEYS or any(
            not isinstance(value, str) or not _SHA256.fullmatch(value)
            for value in self.frozen_hashes.values()
        ):
            raise ReconciliationError("frozen_hash_inventory_incomplete")
        for value in (
            self.protocol_sha256,
            self.runtime_lock_sha256,
            self.frozen_hashes["environment_fingerprint"],
        ):
            if not _SHA256.fullmatch(value):
                raise ReconciliationError("reconciliation_hash_invalid")
        for value in (self.started_at, self.terminal_at, self.reconciled_at):
            if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
                raise ReconciliationError("reconciliation_timestamp_must_be_utc")
        if self.terminal_at < self.started_at or self.reconciled_at < self.terminal_at:
            raise ReconciliationError("reconciliation_timestamp_order_invalid")
        if not self.evidence:
            raise ReconciliationError("reconciliation_evidence_empty")
        object.__setattr__(self, "identities", MappingProxyType(dict(self.identities)))
        object.__setattr__(self, "frozen_hashes", MappingProxyType(dict(self.frozen_hashes)))
        object.__setattr__(
            self, "declared_blockers", tuple(sorted(set(self.declared_blockers), key=str))
        )

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": str(self.run_id),
            "attempt_id": str(self.attempt_id),
            "matrix_key": self.matrix_key.to_document(),
            "identities": dict(sorted(self.identities.items())),
            "frozen_hashes": dict(sorted(self.frozen_hashes.items())),
            "started_at": _utc_timestamp(self.started_at),
            "terminal_at": _utc_timestamp(self.terminal_at),
            "reconciled_at": _utc_timestamp(self.reconciled_at),
            "protocol_sha256": self.protocol_sha256,
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "retention_policy_id": str(self.retention_policy_id),
            "evidence": [
                record.to_document()
                for record in sorted(
                    self.evidence, key=lambda item: canonical_digest(item.to_document())
                )
            ],
            "declared_blockers": [item.value for item in self.declared_blockers],
        }

    def to_report_document(self) -> dict[str, object]:
        """Project only opaque IDs, hashes, and evidence references into a report."""

        return {
            "schema_version": self.schema_version,
            "run_id": str(self.run_id),
            "attempt_id": str(self.attempt_id),
            "matrix_key": self.matrix_key.to_document(),
            "identities": {
                key: canonical_digest({"identity": value})
                for key, value in sorted(self.identities.items())
            },
            "frozen_hashes": dict(sorted(self.frozen_hashes.items())),
            "started_at": _utc_timestamp(self.started_at),
            "terminal_at": _utc_timestamp(self.terminal_at),
            "reconciled_at": _utc_timestamp(self.reconciled_at),
            "protocol_sha256": self.protocol_sha256,
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "retention_policy_id": str(self.retention_policy_id),
            "evidence": [
                record.to_report_document()
                for record in sorted(
                    self.evidence, key=lambda item: canonical_digest(item.to_document())
                )
            ],
            "declared_blockers": [item.value for item in self.declared_blockers],
        }


@dataclass(frozen=True, slots=True)
class AuthorityConflict:
    subject: str
    observations: tuple[tuple[str, str, str], ...]
    blocker: ReconciliationBlocker

    def to_document(self) -> dict[str, object]:
        return {
            "subject_sha256": canonical_digest({"subject": self.subject}),
            "observations": [
                {
                    "authority_sha256": canonical_digest({"authority": authority}),
                    "kind": kind,
                }
                for authority, kind, _ in self.observations
            ],
            "blocker": self.blocker.value,
        }


@dataclass(frozen=True, slots=True)
class AuthoritativeReconciliationState:
    """Ledger facts that must control retry and terminal adjudication."""

    terminal: AttemptTerminal
    terminals: tuple[AttemptTerminal, ...]
    retry_lineage: tuple[tuple[AttemptId, AttemptId], ...]
    retry_authorizations: tuple[RetryAuthorization, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "terminals", tuple(self.terminals))
        object.__setattr__(self, "retry_lineage", tuple(self.retry_lineage))
        object.__setattr__(self, "retry_authorizations", tuple(self.retry_authorizations))


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Immutable, deterministic reconciliation outcome and preserved conflicts."""

    input: ReconciliationInput
    matrix: RequiredEvidenceMatrix
    producer_policy_version: str
    producer_policy_sha256: str
    primary_required: int
    primary_present: int
    blockers: tuple[ReconciliationBlocker, ...]
    conflicts: tuple[AuthorityConflict, ...]
    verified_sources: tuple[ArtifactRef, ...]
    reconciliation_sha256: str

    @property
    def inclusion_status(self) -> InclusionStatus:
        return InclusionStatus.EXCLUDED if self.blockers else InclusionStatus.INCLUDED

    @property
    def reason_code(self) -> str:
        return self.blockers[0].value if self.blockers else "reconciliation_complete"

    def basis_document(self) -> dict[str, object]:
        return {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "artifact_type": "reconciliation_report",
            "input": self.input.to_report_document(),
            "matrix": self.matrix.to_document(),
            "matrix_sha256": self.matrix.sha256,
            "producer_policy_version": self.producer_policy_version,
            "producer_policy_sha256": self.producer_policy_sha256,
            "primary_required": self.primary_required,
            "primary_present": self.primary_present,
            "primary_complete": self.primary_present == self.primary_required,
            "blockers": [item.value for item in self.blockers],
            "conflicts": [item.to_document() for item in self.conflicts],
            "telemetry_transport_only": True,
        }

    def to_document(self) -> dict[str, object]:
        document = self.basis_document()
        document["reconciliation_sha256"] = self.reconciliation_sha256
        return document


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    report: ReconciliationReport
    report_ref: ArtifactRef
    report_manifest_ref: ArtifactRef
    decision: InclusionDecision
    decision_ref: ArtifactRef
    decision_manifest_ref: ArtifactRef
    decision_idempotent: bool


def inclusion_decision_document(
    decision: InclusionDecision, report_ref: ArtifactRef
) -> dict[str, object]:
    """Project the ledger's terminal decision into immutable analysis authority."""
    return {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "artifact_type": "inclusion_decision",
        "inclusion_id": str(decision.id),
        "run_id": str(decision.run_id),
        "status": decision.status.value,
        "reason": decision.reason,
        "reconciliation_sha256": decision.reconciliation_sha256,
        "occurred_at": _utc_timestamp(decision.occurred_at),
        "reconciliation_report_sha256": report_ref.sha256,
    }


@dataclass(frozen=True, slots=True)
class _AuthorityVerification:
    matrix: RequiredEvidenceMatrix
    producer_policy: RequiredEvidenceProducerPolicy
    evidence_projections_match: bool


def reconciliation_authority_from_input(
    request: ReconciliationInput,
) -> ReconciliationAuthority:
    """Build the record that only the control-owned ledger may persist."""

    matrix = required_evidence_matrix(request.matrix_key)
    producer_policy = required_evidence_producer_policy()
    projections: dict[EvidenceKind, ArtifactRef] = {}
    for record in request.evidence:
        if record.kind in projections:
            raise ReconciliationError("reconciliation_authority_projection_duplicate_kind")
        if matrix.requirements[record.kind].mode is RequirementMode.PROHIBITED:
            raise ReconciliationError("reconciliation_authority_prohibited_projection")
        projections[record.kind] = record.projection
    try:
        return ReconciliationAuthority(
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            matrix_key=request.matrix_key,
            matrix_version=matrix.schema_version,
            matrix_sha256=matrix.sha256,
            producer_policy_version=producer_policy.schema_version,
            producer_policy_sha256=producer_policy.sha256,
            identities=request.identities,
            frozen_hashes=request.frozen_hashes,
            started_at=request.started_at,
            terminal_at=request.terminal_at,
            reconciled_at=request.reconciled_at,
            protocol_sha256=request.protocol_sha256,
            runtime_lock_sha256=request.runtime_lock_sha256,
            retention_policy_id=request.retention_policy_id,
            evidence_projections=projections,
        )
    except ValueError as error:
        raise ReconciliationError("reconciliation_authority_invalid") from error


def bind_evidence_projection(
    artifact_store: ArtifactStorePort, record: EvidenceRecord
) -> EvidenceRecord:
    """Persist the canonical envelope a control process must bind into its authority."""

    projection = EvidenceProjection(
        kind=record.kind,
        authority=record.authority,
        status=record.status,
        artifact=record.artifact,
        manifest=record.manifest,
        claims=record.claims,
        unavailable_reason=record.unavailable_reason,
        declared_blockers=record.declared_blockers,
    )
    if SecretBoundaryScanner().scan({"evidence_projection": projection.to_document()}):
        raise ReconciliationError("evidence_projection_secret_boundary_violation")
    reference = artifact_store.put_bytes(
        canonical_bytes(projection.to_document()),
        media_type="application/json",
        classification="evidence_projection",
    )
    return replace(record, projection=reference)


def reconcile_required_evidence(
    request: ReconciliationInput,
    artifact_store: ArtifactStorePort,
    authoritative_state: AuthoritativeReconciliationState | None = None,
    matrix: RequiredEvidenceMatrix | None = None,
    producer_policy: RequiredEvidenceProducerPolicy | None = None,
) -> ReconciliationReport:
    """Compare every matrix requirement against immutable native authorities."""

    matrix = matrix or required_evidence_matrix(request.matrix_key)
    producer_policy = producer_policy or required_evidence_producer_policy()
    records_by_kind: dict[EvidenceKind, list[EvidenceRecord]] = defaultdict(list)
    for record in request.evidence:
        records_by_kind[record.kind].append(record)

    blockers: set[ReconciliationBlocker] = set()
    verified_sources: dict[str, ArtifactRef] = {}
    primary_required = len(matrix.primary_kinds)
    primary_present = 0
    scanner = SecretBoundaryScanner()

    if getattr(artifact_store, "eligible_for_paid_or_study", None) is not True or getattr(
        artifact_store, "provenance", ""
    ).startswith("unpaid_"):
        blockers.add(ReconciliationBlocker.UNQUALIFIED_EVIDENCE)

    for kind in EvidenceKind:
        requirement = matrix.requirements[kind]
        records = records_by_kind.get(kind, [])
        if len(records) > 1:
            blockers.add(ReconciliationBlocker.DUPLICATE)
        if requirement.mode is RequirementMode.PROHIBITED:
            if records:
                blockers.add(ReconciliationBlocker.MALFORMED)
            continue
        if requirement.mode is RequirementMode.CONDITIONALLY_REQUIRED and not records:
            continue
        if not records:
            blockers.add(ReconciliationBlocker.MISSING)
            continue

        record = records[0]
        authority_matches = record.authority in requirement.authorities
        if not authority_matches:
            blockers.add(ReconciliationBlocker.AUTHORITY_CONFLICT)
        verified = _verify_record(
            record,
            request,
            artifact_store,
            scanner,
            blockers,
            verified_sources,
            producer_policy,
        )
        if verified:
            blockers.update(record.declared_blockers)
        if record.status is EvidencePresence.PARTIAL:
            blockers.add(ReconciliationBlocker.PARTIAL)
            continue
        if record.status is EvidencePresence.MALFORMED:
            blockers.add(ReconciliationBlocker.MALFORMED)
            continue
        counts_as_primary = requirement.mode in {
            RequirementMode.PRIMARY_REQUIRED,
            RequirementMode.EXPLICIT_UNAVAILABLE_PERMITTED,
        }
        if record.status is EvidencePresence.UNAVAILABLE:
            if requirement.mode is not RequirementMode.EXPLICIT_UNAVAILABLE_PERMITTED:
                blockers.add(ReconciliationBlocker.UNAVAILABLE_DISALLOWED)
            status_permitted = requirement.mode is RequirementMode.EXPLICIT_UNAVAILABLE_PERMITTED
        else:
            status_permitted = True

        if verified:
            claims_valid = _validate_claims(record, request, blockers, authoritative_state)
        else:
            claims_valid = False
        if (
            counts_as_primary
            and status_permitted
            and authority_matches
            and verified
            and claims_valid
        ):
            primary_present += 1

    conflicts = _authority_conflicts(request.evidence)
    blockers.update(conflict.blocker for conflict in conflicts)
    if authoritative_state is not None:
        blockers.update(_authoritative_lineage_blockers(request, authoritative_state))
    if primary_present != primary_required:
        blockers.add(ReconciliationBlocker.MISSING)

    ordered_blockers = tuple(sorted(blockers, key=str))
    basis = {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "artifact_type": "reconciliation_report",
        "input": request.to_report_document(),
        "matrix": matrix.to_document(),
        "matrix_sha256": matrix.sha256,
        "producer_policy_version": producer_policy.schema_version,
        "producer_policy_sha256": producer_policy.sha256,
        "primary_required": primary_required,
        "primary_present": primary_present,
        "primary_complete": primary_present == primary_required,
        "blockers": [item.value for item in ordered_blockers],
        "conflicts": [item.to_document() for item in conflicts],
        "telemetry_transport_only": True,
    }
    return ReconciliationReport(
        input=request,
        matrix=matrix,
        producer_policy_version=producer_policy.schema_version,
        producer_policy_sha256=producer_policy.sha256,
        primary_required=primary_required,
        primary_present=primary_present,
        blockers=ordered_blockers,
        conflicts=conflicts,
        verified_sources=tuple(sorted(verified_sources.values(), key=lambda item: item.sha256)),
        reconciliation_sha256=canonical_digest(basis),
    )


class ReconciliationService:
    """CAS-first reconciliation service with a typed sole-writer ledger boundary."""

    def __init__(self, artifact_store: ArtifactStorePort, ledger: LedgerPort) -> None:
        self._artifact_store = artifact_store
        self._ledger = ledger

    def reconcile(self, request: ReconciliationInput) -> ReconciliationResult:
        authority_verification = self._require_controlled_authority(request)
        authoritative_state = self._authoritative_state(request)
        report = reconcile_required_evidence(
            request,
            self._artifact_store,
            authoritative_state,
            authority_verification.matrix,
            authority_verification.producer_policy,
        )
        if not authority_verification.evidence_projections_match:
            report = _report_with_blocker(report, ReconciliationBlocker.AUTHORITY_CONFLICT)
        report_ref, report_manifest_ref = self._persist_report(report)
        existing = self._ledger.inclusion_for(request.run_id)
        if existing is not None:
            if (
                existing.reconciliation_sha256 == report.reconciliation_sha256
                and existing.status is report.inclusion_status
            ):
                snapshot = self._ledger.run_state_snapshot(request.run_id)
                if snapshot.state is RunState.RECONCILED:
                    self._append_terminal_state(
                        request,
                        report_ref,
                        report_manifest_ref,
                        existing,
                        snapshot.transition_digest,
                    )
                elif snapshot.state is not RunState(existing.status.value):
                    raise TerminalDecisionConflictError(
                        report_ref=report_ref, report_manifest_ref=report_manifest_ref
                    )
                decision_ref, decision_manifest_ref = self._persist_decision(
                    request, report_ref, existing
                )
                return ReconciliationResult(
                    report,
                    report_ref,
                    report_manifest_ref,
                    existing,
                    decision_ref,
                    decision_manifest_ref,
                    decision_idempotent=True,
                )
            raise TerminalDecisionConflictError(
                report_ref=report_ref, report_manifest_ref=report_manifest_ref
            )

        snapshot = self._ledger.run_state_snapshot(request.run_id)
        if snapshot.state is RunState.SCORED:
            transition = RunTransitionIntent(
                IntentMetadata(
                    IntentId.from_digest(
                        sha256(
                            f"reconciled:{request.run_id}:{report.reconciliation_sha256}".encode()
                        ).hexdigest()
                    ),
                    request.reconciled_at,
                    source_attempt_id=request.attempt_id,
                    expected_prior_state=RunState.SCORED,
                    expected_prior_digest=snapshot.transition_digest,
                    reason_code="required_evidence_reconciled",
                    evidence_refs=(report_ref, report_manifest_ref),
                ),
                request.run_id,
                RunState.SCORED,
                RunState.RECONCILED,
            )
            transition_ack = self._submit(transition, report_ref, report_manifest_ref)
            decision_prior_digest = transition_ack.canonical_payload_digest
        elif snapshot.state is RunState.RECONCILED:
            decision_prior_digest = snapshot.transition_digest
        else:
            raise ReconciliationError("reconciliation_requires_scored_run")

        decision = InclusionDecision(
            InclusionId.from_digest(report.reconciliation_sha256),
            request.run_id,
            report.inclusion_status,
            report.reason_code,
            report.reconciliation_sha256,
            request.reconciled_at,
        )
        intent = InclusionDecisionIntent(
            IntentMetadata(
                IntentId.from_digest(
                    sha256(
                        f"inclusion:{request.run_id}:{report.reconciliation_sha256}".encode()
                    ).hexdigest()
                ),
                request.reconciled_at,
                source_attempt_id=request.attempt_id,
                expected_prior_state=RunState.RECONCILED,
                expected_prior_digest=decision_prior_digest,
                reason_code=decision.reason,
                evidence_refs=(report_ref, report_manifest_ref),
            ),
            decision,
        )
        acknowledgement = self._submit(intent, report_ref, report_manifest_ref)
        self._append_terminal_state(
            request,
            report_ref,
            report_manifest_ref,
            decision,
            decision_prior_digest,
        )
        decision_ref, decision_manifest_ref = self._persist_decision(request, report_ref, decision)
        return ReconciliationResult(
            report,
            report_ref,
            report_manifest_ref,
            decision,
            decision_ref,
            decision_manifest_ref,
            decision_idempotent=acknowledgement.idempotent,
        )

    def _require_controlled_authority(self, request: ReconciliationInput) -> _AuthorityVerification:
        """Reject command input unless the control-owned ledger sealed it first."""

        try:
            authority = self._ledger.reconciliation_authority_for(
                request.run_id, request.attempt_id
            )
        except AttributeError as error:
            raise ReconciliationError("reconciliation_authority_ledger_unavailable") from error
        if authority is None:
            raise ReconciliationError("reconciliation_authority_missing")
        if not isinstance(authority, ReconciliationAuthority):
            raise ReconciliationError("reconciliation_authority_invalid")
        matrix = required_evidence_matrix(request.matrix_key)
        producer_policy = required_evidence_producer_policy()
        if (
            authority.run_id != request.run_id
            or authority.attempt_id != request.attempt_id
            or authority.matrix_key != request.matrix_key
            or dict(authority.identities) != dict(request.identities)
            or dict(authority.frozen_hashes) != dict(request.frozen_hashes)
            or authority.started_at != request.started_at
            or authority.terminal_at != request.terminal_at
            or authority.reconciled_at != request.reconciled_at
            or authority.protocol_sha256 != request.protocol_sha256
            or authority.runtime_lock_sha256 != request.runtime_lock_sha256
            or authority.retention_policy_id != request.retention_policy_id
        ):
            raise ReconciliationError("reconciliation_authority_mismatch")
        if (
            authority.matrix_version != matrix.schema_version
            or authority.matrix_sha256 != matrix.sha256
            or authority.producer_policy_version != producer_policy.schema_version
            or authority.producer_policy_sha256 != producer_policy.sha256
        ):
            raise ReconciliationError("reconciliation_authority_policy_mismatch")
        request_projections: dict[EvidenceKind, ArtifactRef] = {}
        for record in request.evidence:
            if record.kind in request_projections:
                raise ReconciliationError("reconciliation_authority_projection_mismatch")
            request_projections[record.kind] = record.projection
        return _AuthorityVerification(
            matrix,
            producer_policy,
            dict(authority.evidence_projections) == request_projections,
        )

    def _authoritative_state(
        self, request: ReconciliationInput
    ) -> AuthoritativeReconciliationState:
        terminal = self._ledger.attempt_terminal_for(request.attempt_id)
        if terminal is None:
            raise ReconciliationError("authoritative_attempt_terminal_missing")
        if terminal.attempt_id != request.attempt_id:
            raise ReconciliationError("authoritative_attempt_terminal_attempt_mismatch")
        if terminal.run_id != request.run_id:
            raise ReconciliationError("authoritative_attempt_terminal_run_mismatch")
        if terminal.classification is not request.matrix_key.failure_state:
            raise ReconciliationError("authoritative_attempt_terminal_classification_mismatch")
        if _utc_timestamp(terminal.occurred_at) != _utc_timestamp(request.terminal_at):
            raise ReconciliationError("authoritative_attempt_terminal_timestamp_mismatch")
        try:
            terminals = tuple(self._ledger.attempt_terminals_for(request.run_id))
            retry_lineage = tuple(self._ledger.retry_lineage_for(request.run_id))
            retry_authorizations = tuple(self._ledger.retry_authorizations_for(request.run_id))
        except AttributeError as error:
            raise ReconciliationError("authoritative_ledger_lineage_unavailable") from error
        if not any(item.attempt_id == request.attempt_id for item in terminals):
            raise ReconciliationError("authoritative_attempt_terminal_lineage_missing")
        return AuthoritativeReconciliationState(
            terminal,
            terminals,
            retry_lineage,
            retry_authorizations,
        )

    def _append_terminal_state(
        self,
        request: ReconciliationInput,
        report_ref: ArtifactRef,
        report_manifest_ref: ArtifactRef,
        decision: InclusionDecision,
        prior_digest: str | None,
    ) -> None:
        final_transition = RunTransitionIntent(
            IntentMetadata(
                IntentId.from_digest(
                    sha256(
                        (
                            f"terminal-inclusion-state:{request.run_id}:"
                            f"{decision.reconciliation_sha256}"
                        ).encode()
                    ).hexdigest()
                ),
                request.reconciled_at,
                source_attempt_id=request.attempt_id,
                expected_prior_state=RunState.RECONCILED,
                expected_prior_digest=prior_digest,
                reason_code="reconciliation_terminal_decision",
                evidence_refs=(report_ref, report_manifest_ref),
            ),
            request.run_id,
            RunState.RECONCILED,
            RunState(decision.status.value),
        )
        self._submit(final_transition, report_ref, report_manifest_ref)

    def _submit(
        self,
        intent: RunTransitionIntent | InclusionDecisionIntent,
        report_ref: ArtifactRef,
        report_manifest_ref: ArtifactRef,
    ) -> IntentAck:
        try:
            result = self._ledger.submit_intent(intent)
        except LedgerIntentConflictError as error:
            raise TerminalDecisionConflictError(
                "terminal_inclusion_decision_conflict",
                report_ref=report_ref,
                report_manifest_ref=report_manifest_ref,
            ) from error
        if isinstance(result, IntentRejection):
            raise ReconciliationError(f"ledger_reconciliation_rejected_{result.reason_code}")
        return result

    def _persist_report(self, report: ReconciliationReport) -> tuple[ArtifactRef, ArtifactRef]:
        report_bytes = canonical_bytes(report.to_document())
        if SecretBoundaryScanner().scan({"reconciliation_report": report.to_document()}):
            raise ReconciliationError("reconciliation_report_secret_boundary_violation")
        report_ref = self._artifact_store.put_bytes(
            report_bytes,
            media_type="application/json",
            classification="reconciliation_report",
        )
        manifest = ArtifactManifest(
            artifact_id=report_ref.artifact_id,
            kind="reconciliation_report",
            sha256=report_ref.sha256,
            size_bytes=report_ref.size_bytes,
            media_type="application/json",
            created_at=report.input.reconciled_at,
            producer_component="evidence_reconcile",
            producer_version=RECONCILIATION_SCHEMA_VERSION,
            classification="reconciliation_report",
            contains_secrets=False,
            source_artifact_ids=tuple(ref.artifact_id for ref in report.verified_sources),
            retention_policy_id=report.input.retention_policy_id,
            encryption=None,
            scope=ArtifactScope.ATTEMPT,
            run_id=report.input.run_id,
            attempt_id=report.input.attempt_id,
        )
        try:
            manifest_ref = self._artifact_store.write_manifest(manifest)
        except ArtifactIntegrityError as error:
            raise ReconciliationError("reconciliation_report_manifest_unavailable") from error
        return report_ref, manifest_ref

    def _persist_decision(
        self,
        request: ReconciliationInput,
        report_ref: ArtifactRef,
        decision: InclusionDecision,
    ) -> tuple[ArtifactRef, ArtifactRef]:
        """Seal the ledger decision as immutable, read-only analysis authority."""
        payload = canonical_bytes(inclusion_decision_document(decision, report_ref))
        decision_ref = self._artifact_store.put_bytes(
            payload,
            media_type="application/json",
            classification="inclusion_decision",
        )
        manifest = ArtifactManifest(
            artifact_id=decision_ref.artifact_id,
            kind="inclusion_decision",
            sha256=decision_ref.sha256,
            size_bytes=decision_ref.size_bytes,
            media_type="application/json",
            created_at=decision.occurred_at,
            producer_component="evidence_reconcile",
            producer_version=RECONCILIATION_SCHEMA_VERSION,
            classification="inclusion_decision",
            contains_secrets=False,
            source_artifact_ids=(report_ref.artifact_id,),
            retention_policy_id=request.retention_policy_id,
            encryption=None,
            scope=ArtifactScope.ATTEMPT,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
        )
        try:
            manifest_ref = self._artifact_store.write_manifest(manifest)
        except ArtifactIntegrityError as error:
            raise ReconciliationError("inclusion_decision_manifest_unavailable") from error
        return decision_ref, manifest_ref


def load_reconciliation_input(data: bytes) -> ReconciliationInput:
    """Load only canonical, schema-versioned, prompt-free command input."""

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReconciliationError("reconciliation_input_invalid_json") from error
    if not isinstance(document, dict) or canonical_bytes(document) != data:
        raise ReconciliationError("reconciliation_input_not_canonical")
    required_keys = {
        "schema_version",
        "run_id",
        "attempt_id",
        "matrix_key",
        "identities",
        "frozen_hashes",
        "started_at",
        "terminal_at",
        "reconciled_at",
        "protocol_sha256",
        "runtime_lock_sha256",
        "retention_policy_id",
        "evidence",
        "declared_blockers",
    }
    if set(document) != required_keys:
        raise ReconciliationError("reconciliation_input_schema_invalid")
    try:
        key_document = _mapping(document["matrix_key"], "matrix_key")
        key = EvidenceMatrixKey(
            stage=_string(key_document["stage"], "matrix_key.stage"),
            stratum=EvaluationStratum(_string(key_document["stratum"], "matrix_key.stratum")),
            history_mode=HistoryMode(
                _string(key_document["history_mode"], "matrix_key.history_mode")
            ),
            task_state=_string(key_document["task_state"], "matrix_key.task_state"),
            failure_state=AttemptTerminalKind(
                _string(key_document["failure_state"], "matrix_key.failure_state")
            ),
            provider_path=_string(key_document["provider_path"], "matrix_key.provider_path"),
            grader_required=_boolean(key_document["grader_required"], "matrix_key.grader_required"),
            panel_required=_boolean(key_document["panel_required"], "matrix_key.panel_required"),
            adjudication_required=_boolean(
                key_document["adjudication_required"], "matrix_key.adjudication_required"
            ),
        )
        evidence = tuple(
            _record_from_document(item) for item in _list(document["evidence"], "evidence")
        )
        return ReconciliationInput(
            run_id=RunId(_string(document["run_id"], "run_id")),
            attempt_id=AttemptId(_string(document["attempt_id"], "attempt_id")),
            matrix_key=key,
            identities=_string_mapping(document["identities"], "identities"),
            frozen_hashes=_string_mapping(document["frozen_hashes"], "frozen_hashes"),
            started_at=_timestamp(document["started_at"], "started_at"),
            terminal_at=_timestamp(document["terminal_at"], "terminal_at"),
            reconciled_at=_timestamp(document["reconciled_at"], "reconciled_at"),
            protocol_sha256=_string(document["protocol_sha256"], "protocol_sha256"),
            runtime_lock_sha256=_string(document["runtime_lock_sha256"], "runtime_lock_sha256"),
            retention_policy_id=RetentionPolicyId(
                _string(document["retention_policy_id"], "retention_policy_id")
            ),
            evidence=evidence,
            declared_blockers=tuple(
                ReconciliationBlocker(_string(item, "declared_blocker"))
                for item in _list(document["declared_blockers"], "declared_blockers")
            ),
            schema_version=_string(document["schema_version"], "schema_version"),
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ReconciliationError):
            raise
        raise ReconciliationError("reconciliation_input_schema_invalid") from error


def _verify_record(
    record: EvidenceRecord,
    request: ReconciliationInput,
    artifact_store: ArtifactStorePort,
    scanner: SecretBoundaryScanner,
    blockers: set[ReconciliationBlocker],
    verified_sources: dict[str, ArtifactRef],
    producer_policy: RequiredEvidenceProducerPolicy,
) -> bool:
    if record.projection is None:
        blockers.add(ReconciliationBlocker.EVIDENCE_PROJECTION_ABSENT_OR_CORRUPT)
        return False
    try:
        projection_bytes = artifact_store.open_verified(record.projection)
        projection = load_evidence_projection(projection_bytes)
    except (ArtifactIntegrityError, ValueError):
        blockers.add(ReconciliationBlocker.EVIDENCE_PROJECTION_ABSENT_OR_CORRUPT)
        return False
    if scanner.scan(
        {
            f"{record.kind.value}.evidence_projection": projection.to_document(),
            f"{record.kind.value}.evidence_record": record.to_document(),
        }
    ):
        blockers.add(ReconciliationBlocker.CREDENTIAL_LEAK)
        return False
    if not _projection_matches_record(projection, record):
        blockers.add(ReconciliationBlocker.EVIDENCE_PROJECTION_BINDING_CONFLICT)
        return False
    if record.artifact is None or record.manifest is None:
        blockers.add(ReconciliationBlocker.EVIDENCE_PROJECTION_BINDING_CONFLICT)
        return False
    try:
        artifact_bytes = artifact_store.open_verified(record.artifact)
    except ArtifactIntegrityError:
        blockers.add(ReconciliationBlocker.CAS_OR_DIGEST_INVALID)
        return False
    try:
        manifest_bytes = artifact_store.open_verified(record.manifest)
        manifest = parse_manifest(manifest_bytes)
        authoritative_manifest = artifact_store.read_manifest(record.artifact)
    except (ArtifactIntegrityError, AttributeError, ValueError):
        blockers.add(ReconciliationBlocker.MANIFEST_ABSENT_OR_CORRUPT)
        return False
    if (
        manifest.to_dict() != authoritative_manifest.to_dict()
        or manifest.artifact_id != record.artifact.artifact_id
        or manifest.sha256 != record.artifact.sha256
        or manifest.size_bytes != record.artifact.size_bytes
    ):
        blockers.add(ReconciliationBlocker.STALE_MANIFEST)
        return False
    if (
        manifest.kind != record.kind.value
        or manifest.scope is not ArtifactScope.ATTEMPT
        or manifest.run_id != request.run_id
        or manifest.attempt_id != request.attempt_id
    ):
        blockers.add(ReconciliationBlocker.MANIFEST_BINDING_CONFLICT)
        return False
    if not producer_is_authoritative(
        record.authority,
        manifest.producer_component,
        manifest.producer_version,
        producer_policy,
    ):
        blockers.add(ReconciliationBlocker.MANIFEST_PRODUCER_CONFLICT)
        return False
    if scanner.scan(
        {
            f"{record.kind.value}.artifact": artifact_bytes,
            f"{record.kind.value}.claims": record.claims,
        }
    ):
        blockers.add(ReconciliationBlocker.CREDENTIAL_LEAK)
    verified_sources[record.artifact.sha256] = record.artifact
    return True


def _report_with_blocker(
    report: ReconciliationReport, blocker: ReconciliationBlocker
) -> ReconciliationReport:
    """Record an authority-bound input conflict without exposing changed source values."""

    updated = replace(
        report,
        blockers=tuple(sorted({*report.blockers, blocker}, key=str)),
    )
    return replace(updated, reconciliation_sha256=canonical_digest(updated.basis_document()))


def _projection_matches_record(projection: EvidenceProjection, record: EvidenceRecord) -> bool:
    return (
        projection.kind is record.kind
        and projection.authority == record.authority
        and projection.status is record.status
        and projection.artifact == record.artifact
        and projection.manifest == record.manifest
        and dict(projection.claims) == dict(record.claims)
        and projection.unavailable_reason == record.unavailable_reason
        and projection.declared_blockers == record.declared_blockers
    )


def _validate_claims(
    record: EvidenceRecord,
    request: ReconciliationInput,
    blockers: set[ReconciliationBlocker],
    authoritative_state: AuthoritativeReconciliationState | None,
) -> bool:
    required = _REQUIRED_CLAIMS.get(record.kind, frozenset())
    if not required.issubset(record.claims):
        blockers.add(ReconciliationBlocker.MALFORMED)
        return False
    valid = True
    if record.claims.get("run_id", str(request.run_id)) != str(request.run_id) or record.claims.get(
        "attempt_id", str(request.attempt_id)
    ) != str(request.attempt_id):
        blockers.add(ReconciliationBlocker.IDENTITY_CONFLICT)
        valid = False
    if (
        "terminal_state" in record.claims
        and record.claims["terminal_state"] != request.matrix_key.failure_state.value
    ):
        blockers.add(ReconciliationBlocker.TERMINAL_CONFLICT)
        valid = False
    if (
        "environment_fingerprint" in record.claims
        and record.claims["environment_fingerprint"]
        != request.frozen_hashes["environment_fingerprint"]
    ):
        blockers.add(ReconciliationBlocker.ENVIRONMENT_FINGERPRINT_DRIFT)
        valid = False
    if record.kind is EvidenceKind.CLEANUP and record.claims.get("cleanup_status") not in {
        "completed",
        "not_applicable",
    }:
        blockers.add(ReconciliationBlocker.CLEANUP_FAILURE)
        valid = False
    for key, value in record.claims.items():
        if (
            key.startswith("hash.")
            and key.removeprefix("hash.") in request.frozen_hashes
            and value != request.frozen_hashes[key.removeprefix("hash.")]
        ):
            blockers.add(ReconciliationBlocker.IDENTITY_CONFLICT)
            valid = False
    valid = _validate_categorical_claims(record, blockers) and valid
    if record.kind is EvidenceKind.LIFECYCLE and authoritative_state is not None:
        valid = (
            _validate_lifecycle_lineage(record, request, authoritative_state, blockers) and valid
        )
    return valid


def _validate_categorical_claims(
    record: EvidenceRecord, blockers: set[ReconciliationBlocker]
) -> bool:
    valid = True
    for claim, allowed, blocker in _CATEGORICAL_CLAIMS.get(record.kind, ()):
        value = record.claims.get(claim)
        if value not in allowed:
            blockers.add(ReconciliationBlocker.BLOCKER_EVIDENCE_INVALID)
            valid = False
        elif value not in {
            "verified",
            "isolated",
            "canonical",
            "consistent",
            "valid",
        }:
            blockers.add(blocker)
            valid = False
    return valid


def _validate_lifecycle_lineage(
    record: EvidenceRecord,
    request: ReconciliationInput,
    state: AuthoritativeReconciliationState,
    blockers: set[ReconciliationBlocker],
) -> bool:
    inbound = [pair for pair in state.retry_lineage if pair[1] == request.attempt_id]
    expected = "authorized" if inbound else "none"
    if record.claims.get("retry_lineage") != expected:
        blockers.add(ReconciliationBlocker.HIDDEN_RETRY)
        return False
    return True


def _authoritative_lineage_blockers(
    request: ReconciliationInput, state: AuthoritativeReconciliationState
) -> set[ReconciliationBlocker]:
    blockers: set[ReconciliationBlocker] = set()
    terminal_ids = {terminal.attempt_id for terminal in state.terminals}
    if state.terminal.attempt_id not in terminal_ids:
        return {ReconciliationBlocker.HIDDEN_RETRY}

    lineage = set(state.retry_lineage)
    authorized = {
        (authorization.parent_attempt_id, authorization.attempt.id)
        for authorization in state.retry_authorizations
    }
    if len(lineage) != len(state.retry_lineage) or lineage != authorized:
        blockers.add(ReconciliationBlocker.HIDDEN_RETRY)

    connected = {request.attempt_id}
    changed = True
    while changed:
        changed = False
        for parent, child in lineage:
            if parent in connected or child in connected:
                before = len(connected)
                connected.update({parent, child})
                changed = changed or len(connected) != before
    if terminal_ids.difference(connected):
        blockers.add(ReconciliationBlocker.REPLAY)
    lineage_attempt_ids = {attempt_id for link in lineage for attempt_id in link}
    if not lineage_attempt_ids.issubset(terminal_ids):
        blockers.add(ReconciliationBlocker.REPLAY)
    if any(parent == request.attempt_id for parent, _ in lineage):
        blockers.add(ReconciliationBlocker.REPLAY)
    return blockers


def _authority_conflicts(evidence: Sequence[EvidenceRecord]) -> tuple[AuthorityConflict, ...]:
    observations: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for record in evidence:
        for subject, value in record.claims.items():
            observations[subject].append((record.authority, record.kind.value, value))
    conflicts: list[AuthorityConflict] = []
    for subject, values in observations.items():
        if len({value for _, _, value in values}) < 2:
            continue
        if subject in {"run_id", "attempt_id"}:
            blocker = ReconciliationBlocker.IDENTITY_CONFLICT
        elif subject == "terminal_state":
            blocker = ReconciliationBlocker.TERMINAL_CONFLICT
        elif subject == "environment_fingerprint":
            blocker = ReconciliationBlocker.ENVIRONMENT_FINGERPRINT_DRIFT
        else:
            blocker = ReconciliationBlocker.AUTHORITY_CONFLICT
        conflicts.append(
            AuthorityConflict(
                subject,
                tuple(sorted(values, key=lambda item: (item[0], item[1], item[2]))),
                blocker,
            )
        )
    return tuple(sorted(conflicts, key=lambda item: item.subject))


def _record_from_document(value: object) -> EvidenceRecord:
    document = _mapping(value, "evidence record")
    required = {
        "kind",
        "authority",
        "status",
        "artifact",
        "manifest",
        "claims",
        "unavailable_reason",
        "declared_blockers",
        "projection",
    }
    if set(document) != required:
        raise ReconciliationError("evidence_record_schema_invalid")
    return EvidenceRecord(
        kind=EvidenceKind(_string(document["kind"], "evidence.kind")),
        authority=_string(document["authority"], "evidence.authority"),
        status=EvidencePresence(_string(document["status"], "evidence.status")),
        artifact=_ref_from_document(document["artifact"]),
        manifest=_ref_from_document(document["manifest"]),
        claims=_string_mapping(document["claims"], "evidence.claims"),
        unavailable_reason=(
            None
            if document["unavailable_reason"] is None
            else _string(document["unavailable_reason"], "evidence.unavailable_reason")
        ),
        declared_blockers=tuple(
            ReconciliationBlocker(_string(item, "evidence.declared_blocker"))
            for item in _list(document["declared_blockers"], "evidence.declared_blockers")
        ),
        projection=_ref_from_document(document["projection"]),
    )


def _ref_document(reference: ArtifactRef | None) -> dict[str, object] | None:
    if reference is None:
        return None
    return {
        "artifact_id": str(reference.artifact_id),
        "sha256": reference.sha256,
        "size_bytes": reference.size_bytes,
    }


def _ref_from_document(value: object) -> ArtifactRef | None:
    if value is None:
        return None
    document = _mapping(value, "artifact reference")
    if set(document) != {"artifact_id", "sha256", "size_bytes"}:
        raise ReconciliationError("artifact_reference_schema_invalid")
    size = document["size_bytes"]
    if not isinstance(size, int) or isinstance(size, bool):
        raise ReconciliationError("artifact_reference_size_invalid")
    return ArtifactRef(
        ArtifactId(_string(document["artifact_id"], "artifact_id")),
        _string(document["sha256"], "artifact_sha256"),
        size,
    )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReconciliationError("reconciliation_input_duplicate_key")
        result[key] = value
    return result


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReconciliationError(f"{name}_must_be_mapping")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ReconciliationError(f"{name}_must_be_list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReconciliationError(f"{name}_must_be_nonempty_string")
    return value


def _string_mapping(value: object, name: str) -> dict[str, str]:
    mapping = _mapping(value, name)
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in mapping.items()):
        raise ReconciliationError(f"{name}_must_map_strings")
    return dict(mapping)


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ReconciliationError(f"{name}_must_be_boolean")
    return value


def _timestamp(value: object, name: str) -> datetime:
    text = _string(value, name)
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReconciliationError(f"{name}_invalid") from error
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise ReconciliationError(f"{name}_must_be_utc")
    return result


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
