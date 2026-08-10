"""Unpaid conformance coverage for the frozen local BGE artifact authority."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from memrelay_eval.adapters.memrelay import (
    MemrelayProductTreatment,
    ProductProvisionRequest,
    build_framework_process_environments,
)
from memrelay_eval.domain.errors import ConformancePauseError

from memrelay.engine import model_lock
from memrelay.engine.model_lock import (
    EmbeddingModelIntegrityError,
    EmbeddingModelLock,
    materialize_verified_embedding_model,
    verify_embedding_model_cache,
)


class _NoLaunch:
    def __init__(self) -> None:
        self.started = False

    def start(self, request: object) -> object:
        del request
        self.started = True
        raise AssertionError("unverified model must not launch a daemon")


def _request(home: Path, workspace: Path, **overrides: object) -> ProductProvisionRequest:
    daemon, agent, mcp = build_framework_process_environments()
    values: dict[str, object] = {
        "attempt_id": "embedding-lock-contract",
        "home_path": home,
        "workspace_root": workspace,
        "namespace": "namespace",
        "daemon_environment": daemon,
        "agent_environment": agent,
        "mcp_environment": mcp,
    }
    values.update(overrides)
    return ProductProvisionRequest(**values)  # type: ignore[arg-type]


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (home / "config.toml").write_text(
        '[embeddings]\nprovider = "local"\nmodel = "BAAI/bge-small-en-v1.5"\n',
        encoding="utf-8",
    )
    return home, workspace


def test_exact_frozen_model_match_preserves_safe_version_and_lineage(
    tmp_path: Path, frozen_embedding_artifact
) -> None:
    home, _ = _paths(tmp_path)
    frozen_embedding_artifact(home)

    verification = verify_embedding_model_cache(home / "models")

    assert verification.to_evidence() == {
        "model": "BAAI/bge-small-en-v1.5",
        "model_file": "model_optimized.onnx",
        "model_sha256": verification.lock.files["model_optimized.onnx"],
        "source_revision": verification.lock.source_revision,
        "lineage": verification.lock.lineage,
        "file_sha256": dict(verification.lock.files),
    }


@pytest.mark.parametrize("cache_dir", (None, ""))
def test_missing_or_empty_cache_authority_fails_closed(cache_dir: str | None) -> None:
    with pytest.raises(EmbeddingModelIntegrityError):
        verify_embedding_model_cache(cache_dir)


@pytest.mark.parametrize(
    "files",
    (
        {"model_optimized.onnx": ""},
        {"model_optimized.onnx": "not-a-digest"},
    ),
)
def test_empty_or_malformed_digest_cannot_form_an_authority(files: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="embedding model lock"):
        EmbeddingModelLock(
            model_name="BAAI/bge-small-en-v1.5",
            source_repository="Qdrant/bge-small-en-v1.5-onnx-Q",
            source_revision="52398278842ec682c6f32300af41344b1c0b0bb2",
            files=files,
        )


@pytest.mark.parametrize(
    "failure",
    (
        "unknown",
        "mismatch",
        "alternate_path",
        "config_drift",
        "digest_none",
        "digest_empty",
        "digest_malformed",
    ),
)
def test_unverified_model_configuration_never_launches_or_falls_back(
    tmp_path: Path, frozen_embedding_artifact, failure: str
) -> None:
    home, workspace = _paths(tmp_path)
    if failure != "alternate_path":
        artifact = frozen_embedding_artifact(home)
        if failure == "mismatch":
            (artifact / "model_optimized.onnx").write_bytes(b"alternate model bytes")
    else:
        alternate = home / "models" / "alternate-model"
        alternate.mkdir(parents=True)
        (alternate / "model_optimized.onnx").write_bytes(b"favorable alternate")
    if failure == "config_drift":
        (home / "config.toml").write_text(
            '[embeddings]\nprovider = "local"\nmodel = "alternate/model"\n',
            encoding="utf-8",
        )
    launcher = _NoLaunch()
    treatment = MemrelayProductTreatment(launcher=launcher)  # type: ignore[arg-type]
    overrides: dict[str, object] = {}
    if failure == "unknown":
        overrides["embedding_model"] = "unknown/model"
    elif failure == "digest_none":
        overrides["embedding_digest"] = None
    elif failure == "digest_empty":
        overrides["embedding_digest"] = ""
    elif failure == "digest_malformed":
        overrides["embedding_digest"] = "not-a-sha256"

    with pytest.raises(ConformancePauseError):
        asyncio.run(treatment.provision(_request(home, workspace, **overrides)))

    assert not launcher.started


def test_source_mutation_during_snapshot_materialization_fails_closed(
    tmp_path: Path, frozen_embedding_artifact, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _ = _paths(tmp_path)
    artifact = frozen_embedding_artifact(home)
    original = model_lock._copy_model_directory

    def copy_then_mutate(*args: object) -> None:
        original(*args)  # type: ignore[arg-type]
        (artifact / "model_optimized.onnx").write_bytes(b"changed during verification")

    monkeypatch.setattr(model_lock, "_copy_model_directory", copy_then_mutate)

    with pytest.raises(EmbeddingModelIntegrityError, match="digest"):
        materialize_verified_embedding_model(home / "models")
