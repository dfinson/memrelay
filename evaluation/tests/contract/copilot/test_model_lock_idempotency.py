from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest
from memrelay_eval.adapters.copilot.catalog import archive_native_catalog
from memrelay_eval.cli.commands import lock_models
from memrelay_eval.domain.entities import (
    ModelQualification,
    QualificationCaps,
    QualificationTaskResult,
    QualificationUsage,
)
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.orchestration.control import LockRepository, lock_digest, write_model_lock


def _archive():
    return archive_native_catalog(
        b'{"models":[{"id":"native-a"}]}',
        {
            "models": [
                {
                    "id": "native-a",
                    "family": "a",
                    "capabilities": {
                        "tools": True,
                        "permissions": True,
                        "context": 1,
                        "events": True,
                        "cancellation": True,
                        "sessions": True,
                    },
                    "reasoning_effort": "high",
                    "context_tier": "large",
                }
            ]
        },
    )


def _runtime_lock() -> dict[str, object]:
    document: dict[str, object] = {"schema_version": "1.0.0", "runtime": {"sdk_version": "1.0.8"}}
    document["lock_sha256"] = lock_digest(document)
    return document


def _qualification() -> ModelQualification:
    return ModelQualification(
        "native-a",
        tuple(
            QualificationTaskResult(True, 1.0, QualificationUsage(1, 1, 1, 1, 1)) for _ in range(8)
        ),
    )


def _args(*, credits: float = 8) -> Namespace:
    return Namespace(
        credit_cap=credits,
        token_cap=8,
        active_seconds_cap=8,
        wall_seconds_cap=8,
    )


def _seed_lock(repository: LockRepository) -> None:
    runtime = _runtime_lock()
    repository.write("runtime-lock.json", runtime)
    write_model_lock(
        repository,
        runtime,
        _archive(),
        QualificationCaps(8, 8, 8, 8, 8),
        (_qualification(),),
        QualificationUsage(8, 8, 8, 8, 8),
    )


def test_identical_model_lock_request_makes_zero_provider_calls(tmp_path: Path) -> None:
    repository = LockRepository(tmp_path)
    _seed_lock(repository)
    calls = {"catalog": 0, "qualification": 0}

    async def archive_models():
        calls["catalog"] += 1
        return _archive()

    async def qualify(*_):
        calls["qualification"] += 1
        raise AssertionError("qualification must not run for an identical immutable lock")

    assert (
        lock_models(_args(), repository=repository, archive_models=archive_models, qualify=qualify)
        == 0
    )
    assert calls == {"catalog": 0, "qualification": 0}


def test_conflicting_model_lock_request_preserves_prior_bytes(tmp_path: Path) -> None:
    repository = LockRepository(tmp_path)
    _seed_lock(repository)
    prior = repository.read_bytes("model-lock.json")

    with pytest.raises(ConformancePauseError, match="differ"):
        lock_models(_args(credits=9), repository=repository)

    assert repository.read_bytes("model-lock.json") == prior


def test_failed_qualification_does_not_create_a_partial_lock(tmp_path: Path) -> None:
    repository = LockRepository(tmp_path)
    repository.write("runtime-lock.json", _runtime_lock())

    async def archive_models():
        return _archive()

    async def fail_qualification(*_):
        raise ConformancePauseError("qualification_interrupted", "qualification was interrupted")

    with pytest.raises(ConformancePauseError, match="interrupted"):
        lock_models(
            _args(),
            repository=repository,
            archive_models=archive_models,
            qualify=fail_qualification,
        )

    assert repository.read_bytes("model-lock.json") is None
