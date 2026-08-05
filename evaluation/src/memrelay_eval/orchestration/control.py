"""Atomic runtime and model lock persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from memrelay_eval.adapters.copilot.catalog import (
    CatalogArchive,
    ModelSelection,
    qualification_summary,
    select_models,
)
from memrelay_eval.domain.entities import ModelQualification, QualificationCaps, QualificationUsage
from memrelay_eval.domain.errors import ConformancePauseError


class LockRepository:
    """Stores complete immutable lock documents without replacing a valid predecessor."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def read(self, name: str) -> dict[str, object] | None:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConformancePauseError("lock_invalid_json", f"{name} is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ConformancePauseError("lock_shape_invalid", f"{name} must contain a JSON object")
        return value

    def write(self, name: str, document: Mapping[str, object]) -> Path:
        _assert_redacted(document)
        payload = json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        self._root.mkdir(parents=True, exist_ok=True)
        destination = self._path(name)
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=self._root, prefix=f".{name}.", suffix=".tmp"
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def write_catalog_artifact(self, artifact_name: str, data: bytes) -> str:
        """Archive catalog bytes under their content digest without overwrite semantics."""

        digest = sha256(data).hexdigest()
        destination = self._root / f"{artifact_name}-{digest}.json"
        self._root.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != data:
                raise ConformancePauseError(
                    "catalog_artifact_conflict", "catalog artifact path does not match its digest"
                )
            return digest
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=self._root, prefix=f".{artifact_name}.", suffix=".tmp"
        ) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return digest

    def _path(self, name: str) -> Path:
        if name not in {"runtime-lock.json", "model-lock.json"}:
            raise ValueError("unsupported lock document")
        return self._root / name


def lock_digest(document: Mapping[str, object]) -> str:
    """Stable lock linkage digest; lock documents contain no credentials or raw identity."""

    payload = json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(payload).hexdigest()


def write_model_lock(
    repository: LockRepository,
    runtime_lock: Mapping[str, object],
    archive: CatalogArchive,
    caps: QualificationCaps,
    qualifications: tuple[ModelQualification, ...],
    consumption: QualificationUsage,
) -> tuple[dict[str, object], ModelSelection]:
    """Archive a complete catalog and atomically replace only a complete model lock."""

    runtime_lock_sha256 = runtime_lock.get("lock_sha256")
    if not isinstance(runtime_lock_sha256, str):
        raise ConformancePauseError("runtime_lock_invalid", "model lock requires a hashed runtime lock")
    expected_sessions = len(qualifications) * 8
    if caps.session_limit != expected_sessions or consumption.sessions != expected_sessions:
        raise ConformancePauseError(
            "qualification_session_accounting_invalid",
            "model lock requires exactly eight consumed sessions per eligible model",
        )
    if (
        consumption.credits > caps.credit_limit
        or consumption.tokens > caps.token_limit
        or consumption.active_seconds > caps.active_seconds_limit
        or consumption.wall_seconds > caps.wall_seconds_limit
    ):
        raise ConformancePauseError(
            "qualification_consumption_exceeded", "qualification consumption exceeds its frozen cap"
        )
    selection = select_models(archive.catalog, qualifications)
    raw_reference = repository.write_catalog_artifact("native-catalog", archive.raw_bytes)
    projection_reference = repository.write_catalog_artifact(
        "native-catalog-projection", archive.projection_bytes
    )
    selected_models = [
        _locked_model_document(selection.m0),
        *([] if selection.m1 is None else [_locked_model_document(selection.m1)]),
        *([] if selection.m2 is None else [_locked_model_document(selection.m2)]),
        *[_locked_model_document(model) for model in selection.judges],
    ]
    document: dict[str, object] = {
        "schema_version": "1.0.0",
        "runtime_lock_sha256": runtime_lock_sha256,
        "catalog_raw_ref": f"sha256:{raw_reference}",
        "catalog_raw_sha256": archive.catalog.raw_sha256,
        "catalog_projection_ref": f"sha256:{projection_reference}",
        "catalog_projection_sha256": archive.catalog.projection_sha256,
        "eligible_model_count": len(qualifications),
        "qualification_protocol": "copilot-native-eight-task-nonstudy/1.0.0",
        "qualification_caps": {
            "sessions": caps.session_limit,
            "credits": caps.credit_limit,
            "tokens": caps.token_limit,
            "active_seconds": caps.active_seconds_limit,
            "wall_seconds": caps.wall_seconds_limit,
        },
        "qualification_consumption": {
            "sessions": consumption.sessions,
            "credits": consumption.credits,
            "tokens": consumption.tokens,
            "active_seconds": consumption.active_seconds,
            "wall_seconds": consumption.wall_seconds,
        },
        "qualification_evidence": qualification_summary(qualifications),
        "selected_models": selected_models,
        "omissions": dict(selection.omissions),
    }
    document["lock_sha256"] = lock_digest(document)
    repository.write("model-lock.json", document)
    return document, selection


def _locked_model_document(model: object) -> dict[str, object]:
    return {
        "role": model.role,
        "native_id": model.native_id,
        "family": model.family,
        "capabilities": dict(model.capabilities),
        "reasoning_effort": model.reasoning_effort,
        "context_tier": model.context_tier,
    }


def _assert_redacted(value: object, path: str = "") -> None:
    sensitive = {
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "password",
        "secret",
        "credential",
        "prompt",
        "repository",
        "repo",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in sensitive:
                raise ConformancePauseError(
                    "lock_secret_field", f"lock document contains prohibited field at {path or key}"
                )
            _assert_redacted(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_redacted(item, f"{path}[{index}]")
    elif isinstance(value, str) and any(term in value.lower() for term in ("ghp_", "github_pat_")):
        raise ConformancePauseError("lock_secret_value", f"lock document contains credential-like data at {path}")
