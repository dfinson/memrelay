"""Frozen local-embedding artifact authority.

The configured model name is not sufficient provenance: fastembed can otherwise
accept a changed cache entry or download a newer artifact.  This module owns the
only accepted BGE artifact lineage and verifies the complete runtime file set.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType


class EmbeddingModelIntegrityError(RuntimeError):
    """Raised when the frozen local embedding artifact is unavailable or changed."""


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class EmbeddingModelLock:
    """The fixed source lineage and SHA-256s for files loaded by fastembed."""

    model_name: str
    source_repository: str
    source_revision: str
    files: Mapping[str, str]
    model_file: str = "model_optimized.onnx"

    def __post_init__(self) -> None:
        files = dict(self.files)
        if (
            not self.model_name
            or not self.source_repository
            or len(self.source_revision) != 40
            or any(character not in "0123456789abcdef" for character in self.source_revision)
            or self.model_file not in files
            or any(
                not isinstance(name, str)
                or Path(name).name != name
                or not name
                or not _is_sha256(digest)
                for name, digest in files.items()
            )
        ):
            raise ValueError("embedding model lock is invalid")
        object.__setattr__(self, "files", MappingProxyType(files))

    @property
    def lineage(self) -> str:
        """Stable safe provenance identifier for retained evidence."""

        return f"{self.source_repository}@{self.source_revision}"


# qdrant/bge-small-en-v1.5-onnx-q at this exact revision is FastEmbed's source
# for BAAI/bge-small-en-v1.5.  These hashes cover every file FastEmbed loads.
EMBEDDING_MODEL_LOCK = EmbeddingModelLock(
    model_name="BAAI/bge-small-en-v1.5",
    source_repository="Qdrant/bge-small-en-v1.5-onnx-Q",
    source_revision="52398278842ec682c6f32300af41344b1c0b0bb2",
    files={
        "config.json": "13582bcf2effc85b7bf3d3f5532e686bc1c9ce86bb009d10f0ec33cbe92299dd",
        "model_optimized.onnx": "51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431",
        "special_tokens_map.json": (
            "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a"
        ),
        "tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
        "tokenizer_config.json": (
            "0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53"
        ),
    },
)


@dataclass(frozen=True, slots=True)
class VerifiedEmbeddingModel:
    """A verified model cache location with evidence-safe provenance."""

    source_directory: Path
    lock: EmbeddingModelLock

    def to_evidence(self) -> dict[str, object]:
        """Return path-free model identity evidence."""

        return {
            "model": self.lock.model_name,
            "model_file": self.lock.model_file,
            "model_sha256": self.lock.files[self.lock.model_file],
            "source_revision": self.lock.source_revision,
            "lineage": self.lock.lineage,
            "file_sha256": dict(self.lock.files),
        }


def verify_embedding_model_cache(cache_dir: Path | str | None) -> VerifiedEmbeddingModel:
    """Verify only the frozen cache path and all runtime model bytes.

    The expected digest is deliberately not an argument.  A provision request
    cannot nominate a favorable digest, source revision, or alternate path.
    """

    cache_root = _cache_root(cache_dir)
    lock = EMBEDDING_MODEL_LOCK
    source_directory = cache_root / f"models--{lock.source_repository.replace('/', '--')}"
    source_directory = source_directory / "snapshots" / lock.source_revision
    _verify_model_directory(cache_root, source_directory, lock)
    return VerifiedEmbeddingModel(source_directory, lock)


def materialize_verified_embedding_model(cache_dir: Path | str | None) -> VerifiedEmbeddingModel:
    """Copy verified bytes to a fixed private snapshot and recheck the source.

    FastEmbed receives this snapshot rather than the mutable downloader cache.
    Rechecking the source after copying turns a detected replacement during the
    check/copy interval into a hard startup failure.
    """

    cache_root = _cache_root(cache_dir)
    lock = EMBEDDING_MODEL_LOCK
    snapshots = cache_root / ".memrelay-verified-models"
    if snapshots.is_symlink():
        raise EmbeddingModelIntegrityError("embedding_snapshot_root_unsafe")
    snapshots.mkdir(mode=0o700, exist_ok=True)
    snapshots = _require_directory(snapshots, "embedding_snapshot_root_unsafe")
    destination = snapshots / lock.source_revision

    if destination.exists():
        _verify_model_directory(snapshots, destination, lock)
    else:
        source = verify_embedding_model_cache(cache_root)
        temporary: Path | None = Path(tempfile.mkdtemp(prefix=".model-", dir=snapshots))
        try:
            _copy_model_directory(source.source_directory, temporary, source.lock)
            _verify_model_directory(snapshots, temporary, source.lock)
            # Detect source mutation after the first verification and before the
            # immutable snapshot is selected for loading.
            verify_embedding_model_cache(cache_root)
            try:
                os.replace(temporary, destination)
            except FileExistsError:
                _verify_model_directory(snapshots, destination, source.lock)
            else:
                temporary = None
        finally:
            if temporary is not None:
                shutil.rmtree(temporary, ignore_errors=True)

    _verify_model_directory(snapshots, destination, lock)
    return VerifiedEmbeddingModel(destination, lock)


def verify_materialized_embedding_model(model: VerifiedEmbeddingModel) -> None:
    """Recheck the exact snapshot FastEmbed just loaded before it can be exposed."""

    _verify_model_directory(model.source_directory.parent, model.source_directory, model.lock)


def _copy_model_directory(
    source_directory: Path, destination: Path, lock: EmbeddingModelLock
) -> None:
    for name, expected_digest in lock.files.items():
        payload = _read_verified_file(source_directory / name, expected_digest)
        target = destination / name
        with target.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        target.chmod(stat.S_IREAD)
    destination.chmod(stat.S_IREAD | stat.S_IEXEC)


def _verify_model_directory(allowed_root: Path, directory: Path, lock: EmbeddingModelLock) -> None:
    resolved_root = _require_directory(allowed_root, "embedding_cache_missing_or_unsafe")
    try:
        resolved_directory = directory.resolve(strict=True)
    except OSError as error:
        raise EmbeddingModelIntegrityError("embedding_model_path_missing_or_unsafe") from error
    if not resolved_directory.is_dir() or not resolved_directory.is_relative_to(resolved_root):
        raise EmbeddingModelIntegrityError("embedding_model_path_missing_or_unsafe")
    try:
        entries = {entry.name for entry in resolved_directory.iterdir()}
    except OSError as error:
        raise EmbeddingModelIntegrityError("embedding_model_path_missing_or_unsafe") from error
    if entries != set(lock.files):
        raise EmbeddingModelIntegrityError("embedding_model_file_set_mismatch")
    for name, expected_digest in lock.files.items():
        _read_verified_file(resolved_directory / name, expected_digest)


def _read_verified_file(path: Path, expected_digest: str) -> bytes:
    try:
        before = path.stat()
        if not path.is_file():
            raise OSError("not a regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except OSError as error:
        raise EmbeddingModelIntegrityError("embedding_model_file_missing_or_unsafe") from error
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            descriptor = -1
            opened = os.fstat(source.fileno())
            if _file_identity(before) != _file_identity(opened):
                raise EmbeddingModelIntegrityError("embedding_model_file_changed_during_read")
            payload = source.read()
            after = os.fstat(source.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)
    if _file_identity(opened) != _file_identity(after):
        raise EmbeddingModelIntegrityError("embedding_model_file_changed_during_read")
    if sha256(payload).hexdigest() != expected_digest:
        raise EmbeddingModelIntegrityError("embedding_model_digest_mismatch")
    return payload


def _require_directory(path: Path, code: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise EmbeddingModelIntegrityError(code) from error
    if path.is_symlink() or not resolved.is_dir():
        raise EmbeddingModelIntegrityError(code)
    return resolved


def _cache_root(cache_dir: Path | str | None) -> Path:
    if cache_dir is None:
        raise EmbeddingModelIntegrityError("embedding_cache_missing_or_unsafe")
    return _require_directory(Path(cache_dir), "embedding_cache_missing_or_unsafe")


def _file_identity(status: os.stat_result) -> tuple[int, int, int, int]:
    return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns
