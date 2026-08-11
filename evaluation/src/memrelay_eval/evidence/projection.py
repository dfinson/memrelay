"""Canonical evidence projections bound to immutable source artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.ids import ArtifactId
from memrelay_eval.evidence.required import EvidenceKind

EVIDENCE_PROJECTION_SCHEMA_VERSION = "1.0.0"


class EvidencePresence(StrEnum):
    PRESENT = "present"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"
    MALFORMED = "malformed"


class ReconciliationBlocker(StrEnum):
    MISSING = "missing_primary_evidence"
    DUPLICATE = "duplicate_evidence"
    PARTIAL = "partial_evidence"
    MALFORMED = "malformed_evidence"
    UNAVAILABLE_DISALLOWED = "unavailable_disallowed"
    CAS_OR_DIGEST_INVALID = "cas_or_digest_invalid"
    MANIFEST_ABSENT_OR_CORRUPT = "manifest_absent_or_corrupt"
    STALE_MANIFEST = "stale_manifest"
    MANIFEST_BINDING_CONFLICT = "manifest_binding_conflict"
    MANIFEST_PRODUCER_CONFLICT = "manifest_producer_conflict"
    EVIDENCE_PROJECTION_ABSENT_OR_CORRUPT = "evidence_projection_absent_or_corrupt"
    EVIDENCE_PROJECTION_BINDING_CONFLICT = "evidence_projection_binding_conflict"
    UNAUTHORIZED_EVIDENCE = "unauthorized_evidence"
    CREDENTIAL_LEAK = "credential_evidence"
    AUTHORITY_CONFLICT = "authority_conflict"
    IDENTITY_CONFLICT = "identity_conflict"
    TERMINAL_CONFLICT = "terminal_conflict"
    REPLAY = "replay_detected"
    HIDDEN_RETRY = "hidden_retry"
    FAVORABLE_SUBSTITUTION = "favorable_substitution"
    CONTAMINATION = "contamination"
    TAMPER = "tamper"
    GRADING_CONFLICT = "grading_conflict"
    CAUSAL_VALIDITY_CONFLICT = "causal_validity_conflict"
    ENVIRONMENT_FINGERPRINT_DRIFT = "environment_fingerprint_drift"
    CLEANUP_FAILURE = "cleanup_failure"
    UNQUALIFIED_EVIDENCE = "unqualified_evidence"
    BLOCKER_EVIDENCE_INVALID = "blocker_evidence_invalid"


@dataclass(frozen=True, slots=True)
class EvidenceProjection:
    """A versioned source statement bound to raw CAS bytes and its manifest."""

    kind: EvidenceKind
    authority: str
    status: EvidencePresence
    artifact: ArtifactRef | None
    manifest: ArtifactRef | None
    claims: Mapping[str, str]
    unavailable_reason: str | None = None
    declared_blockers: tuple[ReconciliationBlocker, ...] = ()
    schema_version: str = EVIDENCE_PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVIDENCE_PROJECTION_SCHEMA_VERSION:
            raise ValueError("unsupported_evidence_projection_schema_version")
        if not self.authority:
            raise ValueError("evidence_projection_authority_missing")
        if not isinstance(self.status, EvidencePresence):
            raise ValueError("evidence_projection_status_invalid")
        if (self.artifact is None) != (self.manifest is None):
            raise ValueError("evidence_projection_artifact_manifest_pair_required")
        if self.status in (EvidencePresence.PRESENT, EvidencePresence.UNAVAILABLE) and (
            self.artifact is None or self.manifest is None
        ):
            raise ValueError("evidence_projection_durable_reference_required")
        if self.status is EvidencePresence.UNAVAILABLE and not self.unavailable_reason:
            raise ValueError("evidence_projection_unavailable_reason_required")
        if self.status is not EvidencePresence.UNAVAILABLE and self.unavailable_reason is not None:
            raise ValueError("evidence_projection_unavailable_reason_invalid")
        if any(
            not isinstance(key, str) or not key or not isinstance(value, str) or not value
            for key, value in self.claims.items()
        ):
            raise ValueError("evidence_projection_claims_must_be_nonempty_strings")
        object.__setattr__(self, "claims", MappingProxyType(dict(self.claims)))
        object.__setattr__(
            self, "declared_blockers", tuple(sorted(set(self.declared_blockers), key=str))
        )

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact_type": "evidence_projection",
            "kind": self.kind.value,
            "authority": self.authority,
            "status": self.status.value,
            "artifact": _ref_document(self.artifact),
            "manifest": _ref_document(self.manifest),
            "claims": dict(sorted(self.claims.items())),
            "unavailable_reason": self.unavailable_reason,
            "declared_blockers": [item.value for item in self.declared_blockers],
        }


def load_evidence_projection(data: bytes) -> EvidenceProjection:
    """Parse a canonical projection; callers must also verify its CAS reference."""

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("evidence_projection_invalid_json") from error
    if not isinstance(document, dict) or canonical_bytes(document) != data:
        raise ValueError("evidence_projection_not_canonical")
    required = {
        "schema_version",
        "artifact_type",
        "kind",
        "authority",
        "status",
        "artifact",
        "manifest",
        "claims",
        "unavailable_reason",
        "declared_blockers",
    }
    if set(document) != required or document["artifact_type"] != "evidence_projection":
        raise ValueError("evidence_projection_schema_invalid")
    try:
        return EvidenceProjection(
            kind=EvidenceKind(_string(document["kind"], "evidence_projection.kind")),
            authority=_string(document["authority"], "evidence_projection.authority"),
            status=EvidencePresence(_string(document["status"], "evidence_projection.status")),
            artifact=_ref_from_document(document["artifact"]),
            manifest=_ref_from_document(document["manifest"]),
            claims=_string_mapping(document["claims"], "evidence_projection.claims"),
            unavailable_reason=(
                None
                if document["unavailable_reason"] is None
                else _string(
                    document["unavailable_reason"], "evidence_projection.unavailable_reason"
                )
            ),
            declared_blockers=tuple(
                ReconciliationBlocker(_string(item, "evidence_projection.declared_blocker"))
                for item in _list(
                    document["declared_blockers"], "evidence_projection.declared_blockers"
                )
            ),
            schema_version=_string(document["schema_version"], "schema_version"),
        )
    except (KeyError, TypeError, ValueError) as error:
        if str(error).startswith("evidence_projection_"):
            raise
        raise ValueError("evidence_projection_schema_invalid") from error


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
    document = _mapping(value, "evidence_projection.artifact_reference")
    if set(document) != {"artifact_id", "sha256", "size_bytes"}:
        raise ValueError("evidence_projection_artifact_reference_schema_invalid")
    size = document["size_bytes"]
    if not isinstance(size, int) or isinstance(size, bool):
        raise ValueError("evidence_projection_artifact_reference_size_invalid")
    return ArtifactRef(
        ArtifactId(_string(document["artifact_id"], "artifact_id")),
        _string(document["sha256"], "artifact_sha256"),
        size,
    )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("evidence_projection_duplicate_key")
        result[key] = value
    return result


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name}_must_be_mapping")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name}_must_be_list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name}_must_be_nonempty_string")
    return value


def _string_mapping(value: object, name: str) -> dict[str, str]:
    mapping = _mapping(value, name)
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in mapping.items()):
        raise ValueError(f"{name}_must_map_strings")
    return dict(mapping)
