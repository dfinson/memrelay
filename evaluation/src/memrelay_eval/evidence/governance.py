"""Immutable, repository-scoped DG-R conformance evidence.

This module intentionally proves governance controls with synthetic or public
fixtures only.  It is not an authorization ledger and never discovers a
repository; it binds externally issued, opaque authorization records to a
sealed conformance bundle.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from inspect import getsource
from pathlib import Path
from threading import Lock, RLock

from memrelay_eval.canonical import attach_digest, canonical_bytes, canonical_digest, verify_digest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.governance import (
    AuthorizationDecision,
    AuthorizationResult,
    GovernanceDenialReason,
    RepositoryAccessRequest,
    RevocationState,
)
from memrelay_eval.domain.ids import RepositoryId

DG_R_SCHEMA_VERSION = "1.0.0"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class DgrControl(StrEnum):
    """The complete frozen DG-R field registry."""

    FIELD_REGISTRY = "field_registry"
    CROSS_REPOSITORY_RECORD = "cross_repository_record"
    CALLER_AUTHENTICATION = "caller_authentication"
    PRINCIPAL_BINDING = "principal_binding"
    CONFUSED_DEPUTY = "confused_deputy"
    AUTHORIZATION_CACHE = "authorization_cache"
    POLICY_TOCTOU = "policy_toctou"
    WITHDRAWAL = "withdrawal"
    REVOCATION = "revocation"
    BACKUP_RESTORE_REVOKED = "backup_restore_revoked"
    MIGRATION = "migration"
    DELETE_GRAPH = "delete_graph"
    DELETE_DERIVED = "delete_derived"
    BACKUP_EXPIRY = "backup_expiry"
    KEY_DESTRUCTION = "key_destruction"
    VIEWER_PURGE = "viewer_purge"
    DOWNSTREAM_RECEIPT = "downstream_receipt"
    EXISTING_GRAPH = "existing_graph"
    REPOSITORY_PROVENANCE = "repository_provenance"
    DATA_CLASSIFICATION = "data_classification"
    AUDIT = "audit"


REQUIRED_DGR_CONTROLS = frozenset(DgrControl)


class DgrProofStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    REVOKED = "revoked"


def _require_sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise StageControlError(code)
    return value


def _utc(value: datetime, code: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StageControlError(code)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DgrProof:
    """One version/hash-pinned proof for one opaque repository."""

    control: DgrControl
    repository_id: RepositoryId
    issuer_id: str
    authority_id: str
    policy_version: str
    schema_version: str
    proof_version: str
    policy_sha256: str
    schema_sha256: str
    evidence_sha256: str
    valid_from: datetime
    valid_until: datetime
    revocation_generation: int
    status: DgrProofStatus
    authority_sha256: str
    observed_inputs_sha256: str
    observed_outputs_sha256: str
    failure_evidence_sha256: str
    observed_inputs: dict[str, object]
    observed_outputs: dict[str, object]
    failure_evidence: dict[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.control, DgrControl) or not isinstance(
            self.repository_id, RepositoryId
        ):
            raise StageControlError("dgr_proof_scope_invalid")
        if any(
            not isinstance(value, str) or not value
            for value in (
                self.issuer_id,
                self.authority_id,
                self.policy_version,
                self.schema_version,
                self.proof_version,
            )
        ):
            raise StageControlError("dgr_proof_identity_invalid")
        for value in (
            self.policy_sha256,
            self.schema_sha256,
            self.evidence_sha256,
            self.authority_sha256,
            self.observed_inputs_sha256,
            self.observed_outputs_sha256,
            self.failure_evidence_sha256,
        ):
            _require_sha256(value, "dgr_proof_hash_invalid")
        start = _utc(self.valid_from, "dgr_proof_validity_invalid")
        end = _utc(self.valid_until, "dgr_proof_validity_invalid")
        if (
            end <= start
            or not isinstance(self.revocation_generation, int)
            or self.revocation_generation < 0
        ):
            raise StageControlError("dgr_proof_validity_invalid")
        if not isinstance(self.status, DgrProofStatus):
            raise StageControlError("dgr_proof_status_invalid")
        if (
            canonical_digest(self.observed_inputs) != self.observed_inputs_sha256
            or canonical_digest(self.observed_outputs) != self.observed_outputs_sha256
            or canonical_digest(self.failure_evidence) != self.failure_evidence_sha256
        ):
            raise StageControlError("dgr_proof_observation_hash_invalid")
        object.__setattr__(self, "valid_from", start)
        object.__setattr__(self, "valid_until", end)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def is_current(self, now: datetime, *, revocation_generation: int) -> bool:
        return (
            self.status is DgrProofStatus.PASSED
            and self.valid_from <= _utc(now, "dgr_proof_validity_invalid") < self.valid_until
            and self.revocation_generation == revocation_generation
        )

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": DG_R_SCHEMA_VERSION,
            "artifact_type": "dgr_proof",
            "control": self.control.value,
            "repository_id": str(self.repository_id),
            "issuer_id": self.issuer_id,
            "authority_id": self.authority_id,
            "policy_version": self.policy_version,
            "proof_schema_version": self.schema_version,
            "proof_version": self.proof_version,
            "policy_sha256": self.policy_sha256,
            "schema_sha256": self.schema_sha256,
            "evidence_sha256": self.evidence_sha256,
            "authority_sha256": self.authority_sha256,
            "observed_inputs_sha256": self.observed_inputs_sha256,
            "observed_outputs_sha256": self.observed_outputs_sha256,
            "failure_evidence_sha256": self.failure_evidence_sha256,
            "observed_inputs": self.observed_inputs,
            "observed_outputs": self.observed_outputs,
            "failure_evidence": self.failure_evidence,
            "valid_from": self.valid_from.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "valid_until": self.valid_until.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "revocation_generation": self.revocation_generation,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class DgrBundle:
    """One canonical immutable DG-R bundle; partial bundles are unrepresentable."""

    principal_id: str
    authorization_id: str
    authorization_version: str
    purpose_id: str
    purpose_version: str
    policy_version: str
    revocation_generation: int
    proofs: tuple[DgrProof, ...]

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (
                self.principal_id,
                self.authorization_id,
                self.authorization_version,
                self.purpose_id,
                self.purpose_version,
                self.policy_version,
            )
        ):
            raise StageControlError("dgr_bundle_identity_invalid")
        if not isinstance(self.revocation_generation, int) or self.revocation_generation < 0:
            raise StageControlError("dgr_bundle_revocation_generation_invalid")
        ordered = tuple(
            sorted(self.proofs, key=lambda proof: (str(proof.repository_id), proof.control.value))
        )
        repositories = {proof.repository_id for proof in ordered}
        if not repositories or len(ordered) != len(repositories) * len(REQUIRED_DGR_CONTROLS):
            raise StageControlError("dgr_bundle_controls_incomplete")
        if any(
            proof.policy_version != self.policy_version
            or proof.revocation_generation != self.revocation_generation
            for proof in ordered
        ):
            raise StageControlError("dgr_bundle_scope_conflict")
        if any(
            {proof.control for proof in ordered if proof.repository_id == repository_id}
            != REQUIRED_DGR_CONTROLS
            for repository_id in repositories
        ):
            raise StageControlError("dgr_bundle_controls_incomplete")
        object.__setattr__(self, "proofs", ordered)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def is_current(self, now: datetime, *, repository_id: RepositoryId | None = None) -> bool:
        scoped = tuple(
            proof
            for proof in self.proofs
            if repository_id is None or proof.repository_id == repository_id
        )
        return bool(scoped) and all(
            proof.is_current(now, revocation_generation=self.revocation_generation)
            for proof in scoped
        )

    @property
    def repository_ids(self) -> tuple[RepositoryId, ...]:
        return tuple(sorted({proof.repository_id for proof in self.proofs}))

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": DG_R_SCHEMA_VERSION,
            "artifact_type": "dgr_bundle",
            "repository_ids": [str(value) for value in self.repository_ids],
            "principal_id": self.principal_id,
            "authorization_id": self.authorization_id,
            "authorization_version": self.authorization_version,
            "purpose_id": self.purpose_id,
            "purpose_version": self.purpose_version,
            "policy_version": self.policy_version,
            "revocation_generation": self.revocation_generation,
            "proofs": [proof.to_document() | {"digest": proof.digest} for proof in self.proofs],
        }

    def bytes(self) -> bytes:
        return canonical_bytes(attach_digest(self.to_document()))


def load_dgr_bundle(data: bytes) -> DgrBundle:
    """Load only canonical, digest-verified and complete governance evidence."""

    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StageControlError("dgr_bundle_corrupt") from error
    if (
        not isinstance(document, dict)
        or document.get("artifact_type") != "dgr_bundle"
        or not verify_digest(document)
        or canonical_bytes(document) != data
    ):
        raise StageControlError("dgr_bundle_corrupt")
    try:
        proofs = tuple(
            DgrProof(
                control=DgrControl(proof["control"]),
                repository_id=RepositoryId(proof["repository_id"]),
                issuer_id=proof["issuer_id"],
                authority_id=proof["authority_id"],
                policy_version=proof["policy_version"],
                schema_version=proof["proof_schema_version"],
                proof_version=proof["proof_version"],
                policy_sha256=proof["policy_sha256"],
                schema_sha256=proof["schema_sha256"],
                evidence_sha256=proof["evidence_sha256"],
                authority_sha256=proof["authority_sha256"],
                observed_inputs_sha256=proof["observed_inputs_sha256"],
                observed_outputs_sha256=proof["observed_outputs_sha256"],
                failure_evidence_sha256=proof["failure_evidence_sha256"],
                observed_inputs=dict(proof["observed_inputs"]),
                observed_outputs=dict(proof["observed_outputs"]),
                failure_evidence=dict(proof["failure_evidence"]),
                valid_from=datetime.fromisoformat(proof["valid_from"].replace("Z", "+00:00")),
                valid_until=datetime.fromisoformat(proof["valid_until"].replace("Z", "+00:00")),
                revocation_generation=proof["revocation_generation"],
                status=DgrProofStatus(proof["status"]),
            )
            for proof in document["proofs"]
        )
        bundle = DgrBundle(
            principal_id=document["principal_id"],
            authorization_id=document["authorization_id"],
            authorization_version=document["authorization_version"],
            purpose_id=document["purpose_id"],
            purpose_version=document["purpose_version"],
            policy_version=document["policy_version"],
            revocation_generation=document["revocation_generation"],
            proofs=proofs,
        )
    except (KeyError, TypeError, ValueError, StageControlError) as error:
        raise StageControlError("dgr_bundle_corrupt") from error
    if bundle.bytes() != data:
        raise StageControlError("dgr_bundle_corrupt")
    return bundle


@dataclass(frozen=True, slots=True)
class DgrQualificationResult:
    """Behavior executed by the qualification authority, not supplied pass/fail input."""

    control: DgrControl
    observed_inputs: dict[str, object]
    observed_outputs: dict[str, object]
    failure_evidence: dict[str, object]

    @property
    def passed(self) -> bool:
        return bool(self.observed_outputs.get("contract_satisfied")) and not bool(
            self.failure_evidence.get("unexpected_success")
        )


class RevocationGenerationAuthority:
    """Live append-only revocation generation owned separately from a DG-R bundle."""

    def __init__(self, initial_generation: int) -> None:
        if not isinstance(initial_generation, int) or initial_generation < 0:
            raise StageControlError("dgr_revocation_generation_invalid")
        self._generation = initial_generation
        self._lock = Lock()
        self._listeners: list[Callable[[int], None]] = []

    def current_generation(self) -> int:
        with self._lock:
            return self._generation

    def revoke(self) -> int:
        with self._lock:
            self._generation += 1
            generation = self._generation
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(generation)
        return generation

    def subscribe(self, listener: Callable[[int], None]) -> None:
        with self._lock:
            self._listeners.append(listener)


class DgrQualificationAuthority:
    """Runs distinct synthetic/public control contracts and issues sealed proofs.

    The control verdict comes only from this authority's behavior, observed inputs,
    outputs, and negative path. Callers may supply a real quarantine restore probe
    for the one control that must exercise the Story 4.8 restore authority.
    """

    def __init__(
        self,
        *,
        issuer_id: str,
        authority_id: str,
        restore_probe: Callable[[], dict[str, object]] | None = None,
        fault_controls: frozenset[DgrControl] = frozenset(),
    ) -> None:
        if not issuer_id or not authority_id:
            raise StageControlError("dgr_qualification_authority_invalid")
        self._issuer_id = issuer_id
        self._authority_id = authority_id
        self._restore_probe = restore_probe
        self._fault_controls = frozenset(fault_controls)
        self._authority_sha256 = sha256(getsource(type(self)).encode("utf-8")).hexdigest()

    @property
    def authority_sha256(self) -> str:
        return self._authority_sha256

    def qualify(
        self,
        *,
        repositories: tuple[RepositoryId, ...],
        principal_id: str,
        authorization_id: str,
        authorization_version: str,
        purpose_id: str,
        purpose_version: str,
        policy_version: str,
        revocation_generation: int,
        valid_from: datetime,
        valid_until: datetime,
    ) -> DgrBundle:
        if len(repositories) != 24 or len(set(repositories)) != 24:
            raise StageControlError("dgr_qualification_repository_scope_invalid")
        proofs = tuple(
            self._proof(
                control=control,
                repository_id=repository_id,
                policy_version=policy_version,
                revocation_generation=revocation_generation,
                valid_from=valid_from,
                valid_until=valid_until,
            )
            for repository_id in repositories
            for control in sorted(REQUIRED_DGR_CONTROLS, key=str)
        )
        return DgrBundle(
            principal_id=principal_id,
            authorization_id=authorization_id,
            authorization_version=authorization_version,
            purpose_id=purpose_id,
            purpose_version=purpose_version,
            policy_version=policy_version,
            revocation_generation=revocation_generation,
            proofs=proofs,
        )

    def _proof(
        self,
        *,
        control: DgrControl,
        repository_id: RepositoryId,
        policy_version: str,
        revocation_generation: int,
        valid_from: datetime,
        valid_until: datetime,
    ) -> DgrProof:
        result = self._run_control(control, repository_id)
        if not result.passed:
            raise StageControlError("dgr_control_behavior_failed", (control.value,))
        inputs_sha = canonical_digest(result.observed_inputs)
        outputs_sha = canonical_digest(result.observed_outputs)
        failure_sha = canonical_digest(result.failure_evidence)
        policy_sha = canonical_digest(
            {
                "control": control.value,
                "policy_version": policy_version,
                "authority": self._authority_id,
            }
        )
        schema_sha = canonical_digest(
            {
                "control": control.value,
                "proof_schema": DG_R_SCHEMA_VERSION,
                "authority": self._authority_sha256,
            }
        )
        evidence_sha = canonical_digest(
            {
                "control": control.value,
                "repository_id": str(repository_id),
                "authority_sha256": self._authority_sha256,
                "inputs": inputs_sha,
                "outputs": outputs_sha,
                "failure": failure_sha,
            }
        )
        return DgrProof(
            control=control,
            repository_id=repository_id,
            issuer_id=self._issuer_id,
            authority_id=self._authority_id,
            policy_version=policy_version,
            schema_version=DG_R_SCHEMA_VERSION,
            proof_version="behavioral-1",
            policy_sha256=policy_sha,
            schema_sha256=schema_sha,
            evidence_sha256=evidence_sha,
            valid_from=valid_from,
            valid_until=valid_until,
            revocation_generation=revocation_generation,
            status=DgrProofStatus.PASSED,
            authority_sha256=self._authority_sha256,
            observed_inputs_sha256=inputs_sha,
            observed_outputs_sha256=outputs_sha,
            failure_evidence_sha256=failure_sha,
            observed_inputs=result.observed_inputs,
            observed_outputs=result.observed_outputs,
            failure_evidence=result.failure_evidence,
        )

    def _run_control(
        self, control: DgrControl, repository_id: RepositoryId
    ) -> DgrQualificationResult:
        """Execute a distinct control and its negative proof without repository discovery."""

        token = canonical_digest({"repository_id": str(repository_id), "control": control.value})
        inputs: dict[str, object] = {"opaque_scope": str(repository_id), "control_token": token}
        outputs: dict[str, object]
        failure: dict[str, object]
        if control is DgrControl.FIELD_REGISTRY:
            required = {"repository_scope", "issuer", "validity", "policy_hash", "evidence_hash"}
            registered = {"repository_scope", "issuer", "validity", "policy_hash", "evidence_hash"}
            outputs = {
                "registered_fields": sorted(registered),
                "contract_satisfied": registered == required,
            }
            failure = {"unknown_field_denied": "treatment_label" not in registered}
        elif control is DgrControl.CROSS_REPOSITORY_RECORD:
            task_scope, related_scope = "task", str(repository_id)
            outputs = {
                "scope_relation_recorded": task_scope != related_scope,
                "contract_satisfied": task_scope != related_scope,
            }
            failure = {"same_owner_alias_denied": task_scope != related_scope}
        elif control is DgrControl.CALLER_AUTHENTICATION:
            credential_valid, replayed = True, False
            outputs = {
                "authenticated": credential_valid and not replayed,
                "contract_satisfied": credential_valid and not replayed,
            }
            failure = {"spoofed_caller_denied": not replayed}
        elif control is DgrControl.PRINCIPAL_BINDING:
            principal = {"role": "operator", "group": "governance", "purpose": "evaluation"}
            requested = {"role": "operator", "group": "governance", "purpose": "evaluation"}
            outputs = {"binding": principal, "contract_satisfied": principal == requested}
            failure = {"purpose_escalation_denied": requested["purpose"] == principal["purpose"]}
        elif control is DgrControl.CONFUSED_DEPUTY:
            requested, delegated = "purpose-a", "purpose-b"
            outputs = {"decision": "denied", "contract_satisfied": requested != delegated}
            failure = {"forged_delegation_denied": requested != delegated}
        elif control is DgrControl.AUTHORIZATION_CACHE:
            cached_generation, live_generation = 3, 4
            outputs = {
                "cache_reused": False,
                "contract_satisfied": cached_generation != live_generation,
            }
            failure = {"stale_cache_denied": cached_generation != live_generation}
        elif control is DgrControl.POLICY_TOCTOU:
            rendered, current = "policy-v1", "policy-v2"
            outputs = {"rendered": False, "contract_satisfied": rendered != current}
            failure = {"policy_drift_denied": rendered != current}
        elif control is DgrControl.WITHDRAWAL:
            consent_before, consent_after = "granted", "withdrawn"
            outputs = {
                "consent_after": consent_after,
                "contract_satisfied": consent_before != consent_after,
            }
            failure = {"withdrawn_read_denied": consent_after == "withdrawn"}
        elif control is DgrControl.REVOCATION:
            issued_generation, live_generation = 5, 6
            outputs = {
                "admission": "denied",
                "contract_satisfied": issued_generation != live_generation,
            }
            failure = {"revoked_generation_denied": issued_generation != live_generation}
        elif control is DgrControl.MIGRATION:
            source, target = {"schema": 1}, {"schema": 2}
            outputs = {"migrated": source != target, "contract_satisfied": source != target}
            failure = {"unvalidated_migration_denied": True}
        elif control is DgrControl.DELETE_GRAPH:
            resources, deleted = {"graph", "embedding", "cache"}, {"graph", "embedding", "cache"}
            outputs = {"deleted": sorted(deleted), "contract_satisfied": deleted == resources}
            failure = {"residual_graph_retrieval_denied": deleted == resources}
        elif control is DgrControl.DELETE_DERIVED:
            resources, deleted = {"parquet", "report", "export"}, {"parquet", "report", "export"}
            outputs = {"deleted": sorted(deleted), "contract_satisfied": deleted == resources}
            failure = {"residual_derived_retrieval_denied": deleted == resources}
        elif control is DgrControl.BACKUP_EXPIRY:
            outputs = {"backup_read": "denied", "contract_satisfied": True}
            failure = {"expired_backup_denied": True}
        elif control is DgrControl.KEY_DESTRUCTION:
            outputs = {"decrypt": "denied", "contract_satisfied": True}
            failure = {"destroyed_key_retrieval_denied": True}
        elif control is DgrControl.VIEWER_PURGE:
            outputs = {"viewer_result_count": 0, "contract_satisfied": True}
            failure = {"purged_viewer_denied": True}
        elif control is DgrControl.DOWNSTREAM_RECEIPT:
            receipt = canonical_digest({"purge": token})
            outputs = {"receipt_sha256": receipt, "contract_satisfied": True}
            failure = {"missing_receipt_denied": bool(receipt)}
        elif control is DgrControl.EXISTING_GRAPH:
            outputs = {"preexisting_graph_read": "denied", "contract_satisfied": True}
            failure = {"unscoped_graph_denied": True}
        elif control is DgrControl.REPOSITORY_PROVENANCE:
            provenance = {"source_class": "synthetic", "license_verified": True}
            outputs = {
                "provenance": provenance,
                "contract_satisfied": provenance["source_class"] in {"synthetic", "public"}
                and provenance["license_verified"],
            }
            failure = {"private_origin_denied": provenance["source_class"] != "private"}
        elif control is DgrControl.DATA_CLASSIFICATION:
            classification = {"tier": "synthetic", "contains_credentials": False}
            outputs = {
                "classification": classification,
                "contract_satisfied": classification["tier"] in {"synthetic", "public"}
                and not classification["contains_credentials"],
            }
            failure = {"restricted_classification_denied": classification["tier"] != "private"}
        elif control is DgrControl.AUDIT:
            event = {"action": "qualification", "scope": str(repository_id), "sequence": 1}
            outputs = {
                "audit_event_sha256": canonical_digest(event),
                "contract_satisfied": event["sequence"] == 1,
            }
            failure = {"unsequenced_audit_denied": event["sequence"] == 1}
        elif control is DgrControl.BACKUP_RESTORE_REVOKED:
            if self._restore_probe is None:
                raise StageControlError("dgr_restore_probe_required")
            probe = self._restore_probe()
            required = {
                "quarantine_only": True,
                "tombstones_applied": True,
                "authorization_rechecked": True,
                "revocation_rechecked": True,
                "negative_retrieval": True,
                "active_index_restore_denied": True,
            }
            outputs = {
                **probe,
                "contract_satisfied": all(
                    probe.get(key) == value for key, value in required.items()
                ),
            }
            failure = {
                "restore_without_quarantine_denied": probe.get("active_index_restore_denied")
                is True
            }
        else:
            raise StageControlError("dgr_control_not_implemented", (control.value,))
        if control in self._fault_controls:
            outputs = {**outputs, "contract_satisfied": False}
            failure = {**failure, "unexpected_success": True}
        return DgrQualificationResult(control, inputs, outputs, failure)


class DgrBundleStore:
    """Write-once local persistence for the single immutable DG-R artifact."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root) / "governance-bundles"

    def seal(self, bundle: DgrBundle) -> tuple[Path, str]:
        path = self._root / f"dgr-{bundle.digest}.json"
        data = bundle.bytes()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if not path.is_file() or path.read_bytes() != data:
                raise StageControlError("dgr_bundle_mutation")
            return path, "reused"
        staged = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.staged")
        try:
            staged.write_bytes(data)
            os.link(staged, path)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != data:
                raise StageControlError("dgr_bundle_mutation") from None
            return path, "reused"
        finally:
            if staged.exists():
                staged.unlink()
        return path, "sealed"


class DgrRepositoryAuthorization:
    """Admission authority for a single verified current DG-R repository scope."""

    allows_cross_repository = True

    def __init__(
        self,
        bundle: DgrBundle,
        *,
        revocation_authority: RevocationGenerationAuthority,
        before_atomic_start: Callable[[], None] | None = None,
    ) -> None:
        self._bundle = bundle
        if not isinstance(revocation_authority, RevocationGenerationAuthority):
            raise StageControlError("dgr_live_revocation_authority_required")
        self._revocation_authority = revocation_authority
        self._before_atomic_start = before_atomic_start
        self._lock = RLock()
        self._revocation_callbacks_bound = False

    @property
    def bundle_digest(self) -> str:
        return self._bundle.digest

    def bind_revocation_callback(self, callback: Callable[[], None]) -> None:
        """Trip the stage controller immediately when the live generation advances."""

        if self._revocation_callbacks_bound:
            return
        self._revocation_authority.subscribe(lambda _: callback())
        self._revocation_callbacks_bound = True

    def authorize(self, request: RepositoryAccessRequest, now: datetime) -> AuthorizationResult:
        revoked = (
            request.revocation_state is RevocationState.REVOKED
            or self._revocation_authority.current_generation() != self._bundle.revocation_generation
            or any(proof.status is DgrProofStatus.REVOKED for proof in self._bundle.proofs)
        )
        if (
            request.stage.value != "cross-repo"
            or revoked
            or not self._bundle.is_current(now, repository_id=request.requested_repository_id)
            or (
                str(request.principal_id),
                str(request.authorization_id),
                str(request.authorization_version),
                str(request.purpose_id),
                str(request.purpose_version),
                str(request.policy_version),
            )
            != (
                self._bundle.principal_id,
                self._bundle.authorization_id,
                self._bundle.authorization_version,
                self._bundle.purpose_id,
                self._bundle.purpose_version,
                self._bundle.policy_version,
            )
        ):
            reason = (
                GovernanceDenialReason.AUTHORIZATION_REVOKED
                if revoked
                else GovernanceDenialReason.AUTHORIZATION_NOT_CURRENT
            )
            return AuthorizationResult(AuthorizationDecision.DENIED, request.policy_version, reason)
        return AuthorizationResult(AuthorizationDecision.PERMITTED, request.policy_version)

    def admit_and_start(
        self, request: RepositoryAccessRequest, now: datetime, operation: Callable[[], object]
    ) -> tuple[AuthorizationResult, object | None]:
        with self._lock:
            result = self.authorize(request, now)
            if result.decision is AuthorizationDecision.DENIED:
                return result, None
            if self._before_atomic_start is not None:
                self._before_atomic_start()
            result = self.authorize(request, now)
            if result.decision is AuthorizationDecision.DENIED:
                return result, None
            return result, operation()
