"""Control-owned immutable reconciliation authorities."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.ids import ArtifactId, AttemptId, RetentionPolicyId, RunId
from memrelay_eval.domain.states import AttemptTerminalKind, EvaluationStratum, HistoryMode
from memrelay_eval.evidence.required import (
    EvidenceKind,
    EvidenceMatrixKey,
)

RECONCILIATION_AUTHORITY_SCHEMA_VERSION = "1.1.0"
RECONCILIATION_IDENTITY_KEYS = frozenset(
    {
        "experiment_id",
        "run_id",
        "attempt_id",
        "task_id",
        "replicate_id",
        "history_id",
        "sequence_id",
        "repository_id",
        "model_id",
        "assignment_id",
    }
)
RECONCILIATION_FROZEN_HASH_KEYS = frozenset(
    {
        "assignment",
        "catalog",
        "fixture",
        "workspace",
        "prompt",
        "tool_policy",
        "budget",
        "grader",
        "configuration",
        "model",
        "parity",
        "environment_fingerprint",
        "transitions",
    }
)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class ReconciliationAuthority:
    """The control process's immutable binding for one terminal attempt."""

    run_id: RunId
    attempt_id: AttemptId
    matrix_key: EvidenceMatrixKey
    matrix_version: str
    matrix_sha256: str
    producer_policy_version: str
    producer_policy_sha256: str
    identities: Mapping[str, str]
    frozen_hashes: Mapping[str, str]
    started_at: datetime
    terminal_at: datetime
    reconciled_at: datetime
    protocol_sha256: str
    runtime_lock_sha256: str
    retention_policy_id: RetentionPolicyId
    evidence_projections: Mapping[EvidenceKind, ArtifactRef]
    schema_version: str = RECONCILIATION_AUTHORITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RECONCILIATION_AUTHORITY_SCHEMA_VERSION:
            raise ValueError("unsupported_reconciliation_authority_schema_version")
        if self.matrix_key.stage is None:
            raise ValueError("reconciliation_authority_stage_missing")
        if not self.matrix_version or not _SHA256.fullmatch(self.matrix_sha256):
            raise ValueError("reconciliation_authority_matrix_policy_invalid")
        if not self.producer_policy_version or not _SHA256.fullmatch(self.producer_policy_sha256):
            raise ValueError("reconciliation_authority_producer_policy_invalid")
        if set(self.identities) != RECONCILIATION_IDENTITY_KEYS:
            raise ValueError("reconciliation_authority_identity_inventory_incomplete")
        if (
            self.identities["run_id"] != str(self.run_id)
            or self.identities["attempt_id"] != str(self.attempt_id)
            or any(not isinstance(value, str) or not value for value in self.identities.values())
        ):
            raise ValueError("reconciliation_authority_identity_inventory_conflict")
        if set(self.frozen_hashes) != RECONCILIATION_FROZEN_HASH_KEYS or any(
            not isinstance(value, str) or not _SHA256.fullmatch(value)
            for value in self.frozen_hashes.values()
        ):
            raise ValueError("reconciliation_authority_frozen_hash_inventory_incomplete")
        if any(
            not _SHA256.fullmatch(value)
            for value in (
                self.protocol_sha256,
                self.runtime_lock_sha256,
                self.frozen_hashes["environment_fingerprint"],
            )
        ):
            raise ValueError("reconciliation_authority_hash_invalid")
        if any(
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)
            for value in (self.started_at, self.terminal_at, self.reconciled_at)
        ):
            raise ValueError("reconciliation_authority_timestamp_must_be_utc")
        if self.terminal_at < self.started_at or self.reconciled_at < self.terminal_at:
            raise ValueError("reconciliation_authority_timestamp_order_invalid")
        if not self.evidence_projections:
            raise ValueError("reconciliation_authority_projections_empty")
        if any(
            not isinstance(kind, EvidenceKind) or not isinstance(reference, ArtifactRef)
            for kind, reference in self.evidence_projections.items()
        ):
            raise ValueError("reconciliation_authority_projection_inventory_invalid")
        object.__setattr__(self, "identities", MappingProxyType(dict(self.identities)))
        object.__setattr__(self, "frozen_hashes", MappingProxyType(dict(self.frozen_hashes)))
        object.__setattr__(
            self, "evidence_projections", MappingProxyType(dict(self.evidence_projections))
        )

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": str(self.run_id),
            "attempt_id": str(self.attempt_id),
            "matrix_key": self.matrix_key.to_document(),
            "matrix_version": self.matrix_version,
            "matrix_sha256": self.matrix_sha256,
            "producer_policy_version": self.producer_policy_version,
            "producer_policy_sha256": self.producer_policy_sha256,
            "identities": dict(sorted(self.identities.items())),
            "frozen_hashes": dict(sorted(self.frozen_hashes.items())),
            "started_at": _utc_timestamp(self.started_at),
            "terminal_at": _utc_timestamp(self.terminal_at),
            "reconciled_at": _utc_timestamp(self.reconciled_at),
            "protocol_sha256": self.protocol_sha256,
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "retention_policy_id": str(self.retention_policy_id),
            "evidence_projections": [
                {"kind": kind.value, "projection": _ref_document(reference)}
                for kind, reference in sorted(
                    self.evidence_projections.items(), key=lambda item: item[0].value
                )
            ],
        }


def load_reconciliation_authority(data: bytes) -> ReconciliationAuthority:
    """Load only the exact canonical authority persisted by the control ledger."""

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("reconciliation_authority_invalid_json") from error
    if not isinstance(document, dict) or canonical_bytes(document) != data:
        raise ValueError("reconciliation_authority_not_canonical")
    required = {
        "schema_version",
        "run_id",
        "attempt_id",
        "matrix_key",
        "matrix_version",
        "matrix_sha256",
        "producer_policy_version",
        "producer_policy_sha256",
        "identities",
        "frozen_hashes",
        "started_at",
        "terminal_at",
        "reconciled_at",
        "protocol_sha256",
        "runtime_lock_sha256",
        "retention_policy_id",
        "evidence_projections",
    }
    if set(document) != required:
        raise ValueError("reconciliation_authority_schema_invalid")
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
                key_document["adjudication_required"],
                "matrix_key.adjudication_required",
            ),
        )
        projections: dict[EvidenceKind, ArtifactRef] = {}
        for item in _list(document["evidence_projections"], "evidence_projections"):
            projection = _mapping(item, "evidence_projection")
            if set(projection) != {"kind", "projection"}:
                raise ValueError("reconciliation_authority_projection_schema_invalid")
            kind = EvidenceKind(_string(projection["kind"], "evidence_projection.kind"))
            if kind in projections:
                raise ValueError("reconciliation_authority_projection_duplicate_kind")
            projections[kind] = _ref_from_document(projection["projection"])
        return ReconciliationAuthority(
            run_id=RunId(_string(document["run_id"], "run_id")),
            attempt_id=AttemptId(_string(document["attempt_id"], "attempt_id")),
            matrix_key=key,
            matrix_version=_string(document["matrix_version"], "matrix_version"),
            matrix_sha256=_string(document["matrix_sha256"], "matrix_sha256"),
            producer_policy_version=_string(
                document["producer_policy_version"], "producer_policy_version"
            ),
            producer_policy_sha256=_string(
                document["producer_policy_sha256"], "producer_policy_sha256"
            ),
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
            evidence_projections=projections,
            schema_version=_string(document["schema_version"], "schema_version"),
        )
    except (KeyError, TypeError, ValueError) as error:
        if str(error).startswith("reconciliation_authority_"):
            raise
        raise ValueError("reconciliation_authority_schema_invalid") from error


def _ref_document(reference: ArtifactRef) -> dict[str, object]:
    return {
        "artifact_id": str(reference.artifact_id),
        "sha256": reference.sha256,
        "size_bytes": reference.size_bytes,
    }


def _ref_from_document(value: object) -> ArtifactRef:
    document = _mapping(value, "artifact reference")
    if set(document) != {"artifact_id", "sha256", "size_bytes"}:
        raise ValueError("reconciliation_authority_artifact_reference_schema_invalid")
    size = document["size_bytes"]
    if not isinstance(size, int) or isinstance(size, bool):
        raise ValueError("reconciliation_authority_artifact_reference_size_invalid")
    return ArtifactRef(
        ArtifactId(_string(document["artifact_id"], "artifact_id")),
        _string(document["sha256"], "artifact_sha256"),
        size,
    )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("reconciliation_authority_duplicate_key")
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


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name}_must_be_boolean")
    return value


def _timestamp(value: object, name: str) -> datetime:
    text = _string(value, name)
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name}_invalid") from error
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise ValueError(f"{name}_must_be_utc")
    return result


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
