"""Canonical ArtifactManifest serialization and strict version 1.0.0 parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from memrelay_eval.canonical import CanonicalizationError, canonical_bytes, canonical_digest
from memrelay_eval.domain.entities import ArtifactManifest
from memrelay_eval.domain.errors import InvalidArtifactManifestError
from memrelay_eval.domain.ids import (
    ArtifactId,
    AttemptId,
    ExperimentId,
    RetentionPolicyId,
    RunId,
)
from memrelay_eval.domain.states import ArtifactScope

_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "artifact_id",
        "kind",
        "sha256",
        "size_bytes",
        "media_type",
        "created_at",
        "producer",
        "classification",
        "contains_secrets",
        "source_artifact_ids",
        "retention_policy_id",
        "encryption",
        "scope",
        "experiment_id",
        "run_id",
        "attempt_id",
    }
)
_PRODUCER_KEYS = frozenset({"component", "version"})


def canonical_json_bytes(value: object) -> bytes:
    """Return the sole evaluator JSON identity projection for JSON-safe records."""
    _reject_manifest_floats(value)
    try:
        return canonical_bytes(value)
    except CanonicalizationError as error:
        raise InvalidArtifactManifestError(
            "canonical manifest JSON contains an unsupported value"
        ) from error


def _reject_manifest_floats(value: object) -> None:
    if isinstance(value, float):
        raise InvalidArtifactManifestError("canonical manifest JSON does not permit floats")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_manifest_floats(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_manifest_floats(child)


def manifest_bytes(manifest: ArtifactManifest) -> bytes:
    """Serialize a validated manifest as canonical UTF-8 JSON."""
    return canonical_json_bytes(manifest.to_dict())


def parse_manifest(data: bytes) -> ArtifactManifest:
    """Parse only an exact canonical 1.0.0 manifest representation."""
    try:
        decoded = data.decode("utf-8")
        value = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidArtifactManifestError("manifest must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != data:
        raise InvalidArtifactManifestError("manifest must use canonical JSON encoding")

    try:
        _require_exact_keys(value, _MANIFEST_KEYS, "manifest")
        producer = _mapping(value["producer"], "producer")
        _require_exact_keys(producer, _PRODUCER_KEYS, "producer")
        encryption_value = value["encryption"]
        encryption = (
            None if encryption_value is None else _string_mapping(encryption_value, "encryption")
        )
        created_at = datetime.fromisoformat(
            _string(value["created_at"], "created_at").replace("Z", "+00:00")
        )
        if created_at.tzinfo is None or created_at.utcoffset() != UTC.utcoffset(created_at):
            raise InvalidArtifactManifestError("created_at must be UTC")
        source_ids = tuple(
            ArtifactId(_string(item, "source_artifact_ids item"))
            for item in _sequence(value["source_artifact_ids"], "source_artifact_ids")
        )
        manifest = ArtifactManifest(
            artifact_id=ArtifactId(_string(value["artifact_id"], "artifact_id")),
            kind=_string(value["kind"], "kind"),
            sha256=_string(value["sha256"], "sha256"),
            size_bytes=_integer(value["size_bytes"], "size_bytes"),
            media_type=_string(value["media_type"], "media_type"),
            created_at=created_at,
            producer_component=_string(producer["component"], "producer.component"),
            producer_version=_string(producer["version"], "producer.version"),
            classification=_string(value["classification"], "classification"),
            contains_secrets=_boolean(value["contains_secrets"], "contains_secrets"),
            source_artifact_ids=source_ids,
            retention_policy_id=RetentionPolicyId(
                _string(value["retention_policy_id"], "retention_policy_id")
            ),
            encryption=encryption,
            scope=ArtifactScope(_string(value["scope"], "scope")),
            experiment_id=_optional_identifier(
                value["experiment_id"], ExperimentId, "experiment_id"
            ),
            run_id=_optional_identifier(value["run_id"], RunId, "run_id"),
            attempt_id=_optional_identifier(value["attempt_id"], AttemptId, "attempt_id"),
            schema_version=_string(value["schema_version"], "schema_version"),
        )
        if manifest_bytes(manifest) != data:
            raise InvalidArtifactManifestError("manifest does not match its canonical authority")
        return manifest
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, InvalidArtifactManifestError):
            raise
        raise InvalidArtifactManifestError("manifest does not satisfy schema 1.0.0") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidArtifactManifestError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidArtifactManifestError(f"{name} must be an object")
    return value


def _require_exact_keys(value: Mapping[str, object], expected: frozenset[str], name: str) -> None:
    if set(value) != expected:
        raise InvalidArtifactManifestError(f"{name} keys do not match schema 1.0.0")


def _string_mapping(value: object, name: str) -> dict[str, str]:
    mapping = _mapping(value, name)
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in mapping.items()):
        raise InvalidArtifactManifestError(f"{name} must map strings to strings")
    return dict(mapping)


def _sequence(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise InvalidArtifactManifestError(f"{name} must be an array")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidArtifactManifestError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidArtifactManifestError(f"{name} must be a non-negative integer")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidArtifactManifestError(f"{name} must be a boolean")
    return value


def _optional_identifier(
    value: object,
    identifier_type: type[ExperimentId] | type[RunId] | type[AttemptId],
    name: str,
) -> ExperimentId | RunId | AttemptId | None:
    if value is None:
        return None
    return identifier_type(_string(value, name))


def reconciliation_command_manifest(
    *,
    stage: str,
    terminal_status: str,
    exit_code: int,
    input_hashes: Mapping[str, str],
    output_hashes: Mapping[str, str],
    runtime_lock_sha256: str | None,
    protocol_sha256: str | None,
    error_code: str | None,
) -> bytes:
    """Create the canonical terminal manifest for a noninteractive reconcile command."""

    document = {
        "schema_version": "1.0.0",
        "command": "reconcile",
        "stage": stage,
        "terminal_status": terminal_status,
        "exit_code": exit_code,
        "input_hashes": dict(sorted(input_hashes.items())),
        "output_hashes": dict(sorted(output_hashes.items())),
        "runtime_lock_sha256": runtime_lock_sha256,
        "protocol_sha256": protocol_sha256,
        "error_code": error_code,
    }
    document["digest"] = canonical_digest(document)
    return canonical_json_bytes(document)


def stage_command_manifest(
    *,
    command: str,
    stage: str,
    terminal_status: str,
    exit_code: int,
    input_hashes: Mapping[str, str],
    output_hashes: Mapping[str, str],
    runtime_lock_sha256: str | None,
    protocol_sha256: str | None,
    error_code: str | None,
) -> bytes:
    """Create the shared canonical command manifest for a noninteractive stage command.

    The shape matches ``reconciliation_command_manifest`` so every required
    command emits one uniform, digest-bound terminal record.
    """

    document = {
        "schema_version": "1.0.0",
        "command": command,
        "stage": stage,
        "terminal_status": terminal_status,
        "exit_code": exit_code,
        "input_hashes": dict(sorted(input_hashes.items())),
        "output_hashes": dict(sorted(output_hashes.items())),
        "runtime_lock_sha256": runtime_lock_sha256,
        "protocol_sha256": protocol_sha256,
        "error_code": error_code,
    }
    document["digest"] = canonical_digest(document)
    return canonical_json_bytes(document)
