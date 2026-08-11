"""Hermetic first-use coverage for the frozen embedding-model bootstrap."""

from __future__ import annotations

import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from memrelay.engine import model_lock
from memrelay.engine.embedder import LocalEmbedder, LocalEmbedderError
from memrelay.engine.model_lock import (
    EmbeddingModelIntegrityError,
    materialize_verified_embedding_model,
)


def _locked_payloads(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    payloads = {
        name: f"locked-bootstrap-artifact:{name}\n".encode()
        for name in model_lock.EMBEDDING_MODEL_LOCK.files
    }
    lock = replace(
        model_lock.EMBEDDING_MODEL_LOCK,
        files={name: sha256(payload).hexdigest() for name, payload in payloads.items()},
    )
    monkeypatch.setattr(model_lock, "EMBEDDING_MODEL_LOCK", lock)
    return payloads


def _write_artifact(directory: Path, payloads: dict[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (directory / name).write_bytes(payload)


def _raw_directory(cache_dir: Path) -> Path:
    lock = model_lock.EMBEDDING_MODEL_LOCK
    return (
        cache_dir
        / f"models--{lock.source_repository.replace('/', '--')}"
        / "snapshots"
        / lock.source_revision
    )


def _install_fastembed(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, object]]) -> None:
    module = types.ModuleType("fastembed")

    def text_embedding(*_args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    module.TextEmbedding = text_embedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", module)


def test_local_embedder_bootstraps_absent_locked_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real local-embedder path downloads only through the frozen lock seam."""

    payloads = _locked_payloads(monkeypatch)
    downloads: list[object] = []
    fastembed_calls: list[dict[str, object]] = []
    _install_fastembed(monkeypatch, fastembed_calls)

    def download(staging: Path, lock: object) -> None:
        downloads.append(lock)
        _write_artifact(staging, payloads)

    monkeypatch.setattr(model_lock, "_download_locked_embedding_model", download)
    cache_dir = tmp_path / "home" / "models"

    LocalEmbedder(cache_dir=cache_dir)

    snapshot = (
        cache_dir / ".memrelay-verified-models" / model_lock.EMBEDDING_MODEL_LOCK.source_revision
    )
    assert downloads == [model_lock.EMBEDDING_MODEL_LOCK]
    assert fastembed_calls[0]["local_files_only"] is True
    assert fastembed_calls[0]["specific_model_path"] == str(snapshot)
    assert snapshot.is_dir()


def test_warm_verified_cache_loads_without_downloader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A verified cache is usable offline and never retries the downloader."""

    payloads = _locked_payloads(monkeypatch)
    cache_dir = tmp_path / "models"
    _write_artifact(_raw_directory(cache_dir), payloads)
    fastembed_calls: list[dict[str, object]] = []
    _install_fastembed(monkeypatch, fastembed_calls)

    def offline(*_args: object) -> None:
        raise AssertionError("warm verified cache must not call the downloader")

    monkeypatch.setattr(model_lock, "_download_locked_embedding_model", offline)

    LocalEmbedder(cache_dir=cache_dir)

    assert len(fastembed_calls) == 1


@pytest.mark.parametrize("failure", ("network", "partial", "tampered"))
def test_failed_bootstrap_never_starts_fastembed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    """Network and byte-integrity failures fail before FastEmbed can load anything."""

    payloads = _locked_payloads(monkeypatch)
    fastembed_calls: list[dict[str, object]] = []
    _install_fastembed(monkeypatch, fastembed_calls)

    def download(staging: Path, _lock: object) -> None:
        if failure == "network":
            raise EmbeddingModelIntegrityError("embedding_download_failed")
        written = payloads if failure == "tampered" else dict(list(payloads.items())[1:])
        _write_artifact(staging, written)
        if failure == "tampered":
            (staging / "model_optimized.onnx").write_bytes(b"tampered")

    monkeypatch.setattr(model_lock, "_download_locked_embedding_model", download)

    with pytest.raises(LocalEmbedderError):
        LocalEmbedder(cache_dir=tmp_path / "models")

    assert not fastembed_calls
    assert not _raw_directory(tmp_path / "models").exists()


def test_wrong_source_revision_rejects_before_snapshot_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The downloader verifies the Hub's resolved commit before accepting bytes."""

    _locked_payloads(monkeypatch)
    calls: list[str] = []

    class API:
        def model_info(self, **_kwargs: object) -> object:
            return types.SimpleNamespace(sha="0" * 40)

    module = types.ModuleType("huggingface_hub")
    module.HfApi = API  # type: ignore[attr-defined]

    def snapshot_download(**_kwargs: object) -> str:
        calls.append("snapshot")
        raise AssertionError("wrong revision must prevent download")

    module.snapshot_download = snapshot_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)

    with pytest.raises(EmbeddingModelIntegrityError, match="source_revision"):
        materialize_verified_embedding_model(tmp_path / "models")

    assert not calls


def test_existing_mismatched_raw_cache_is_never_redownloaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Existing evidence of a mismatch is rejected rather than replaced."""

    payloads = _locked_payloads(monkeypatch)
    raw = _raw_directory(tmp_path / "models")
    _write_artifact(raw, payloads)
    (raw / "model_optimized.onnx").write_bytes(b"mismatched")
    downloads: list[object] = []
    fastembed_calls: list[dict[str, object]] = []
    _install_fastembed(monkeypatch, fastembed_calls)
    monkeypatch.setattr(
        model_lock,
        "_download_locked_embedding_model",
        lambda *_args: downloads.append(object()),
    )

    with pytest.raises(LocalEmbedderError):
        LocalEmbedder(cache_dir=tmp_path / "models")

    assert not downloads
    assert not fastembed_calls


def test_concurrent_bootstrap_and_materialization_publish_one_verified_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same-process concurrent first use is serialized and both callers get the snapshot."""

    payloads = _locked_payloads(monkeypatch)
    downloads: list[object] = []
    start = threading.Barrier(2)

    def download(staging: Path, lock: object) -> None:
        downloads.append(lock)
        _write_artifact(staging, payloads)

    def materialize() -> Path:
        start.wait()
        return materialize_verified_embedding_model(tmp_path / "models").source_directory

    monkeypatch.setattr(model_lock, "_download_locked_embedding_model", download)
    with ThreadPoolExecutor(max_workers=2) as executor:
        snapshots = list(executor.map(lambda _index: materialize(), range(2)))

    assert downloads == [model_lock.EMBEDDING_MODEL_LOCK]
    assert snapshots[0] == snapshots[1]
    model_lock.verify_materialized_embedding_model(
        materialize_verified_embedding_model(tmp_path / "models")
    )
