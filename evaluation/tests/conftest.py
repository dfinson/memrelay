"""Shared deterministic local model fixtures for unpaid evaluator tests."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from memrelay.engine import model_lock


@pytest.fixture
def frozen_embedding_artifact(monkeypatch: pytest.MonkeyPatch):
    """Install the sole fake artifact authority and materialize it under a home."""

    payloads = {
        name: f"unpaid-fake-bge-artifact:{name}\n".encode()
        for name in model_lock.EMBEDDING_MODEL_LOCK.files
    }
    lock = replace(
        model_lock.EMBEDDING_MODEL_LOCK,
        files={name: sha256(payload).hexdigest() for name, payload in payloads.items()},
    )
    monkeypatch.setattr(model_lock, "EMBEDDING_MODEL_LOCK", lock)

    def install(home: Path) -> Path:
        directory = (
            home
            / "models"
            / f"models--{lock.source_repository.replace('/', '--')}"
            / "snapshots"
            / lock.source_revision
        )
        directory.mkdir(parents=True)
        for name, payload in payloads.items():
            (directory / name).write_bytes(payload)
        return directory

    return install
