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
from pathlib import Path
from threading import RLock

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
        for value in (self.policy_sha256, self.schema_sha256, self.evidence_sha256):
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
        revocation_generation: Callable[[], int] | None = None,
    ) -> None:
        self._bundle = bundle
        self._revocation_generation = revocation_generation or (
            lambda: bundle.revocation_generation
        )
        self._lock = RLock()

    @property
    def bundle_digest(self) -> str:
        return self._bundle.digest

    def authorize(self, request: RepositoryAccessRequest, now: datetime) -> AuthorizationResult:
        revoked = (
            request.revocation_state is RevocationState.REVOKED
            or self._revocation_generation() != self._bundle.revocation_generation
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
            return (
                (result, operation())
                if result.decision is AuthorizationDecision.PERMITTED
                else (result, None)
            )
