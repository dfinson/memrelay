from __future__ import annotations

import pytest
from memrelay_eval.adapters.copilot.catalog import archive_native_catalog
from memrelay_eval.domain.entities import (
    ModelQualification,
    QualificationCaps,
    QualificationTaskResult,
    QualificationUsage,
    RuntimeIdentity,
)
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.orchestration.control import LockRepository, lock_digest, write_model_lock
from memrelay_eval.orchestration.stages import verify_stage_locks


def archive():
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


def qualification() -> ModelQualification:
    return ModelQualification(
        "native-a",
        tuple(
            QualificationTaskResult(True, 1.0, QualificationUsage(1, 1, 1, 1, 1)) for _ in range(8)
        ),
    )


def runtime_lock() -> dict[str, object]:
    runtime = RuntimeIdentity(
        "1.0.8",
        "github_copilot_sdk-1.0.8-py3-none-any.whl",
        "7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa",
        "runtime-1",
        "a" * 64,
        "stdio",
        "copilot_subscription",
        "b" * 64,
    )
    document: dict[str, object] = {
        "schema_version": "1.0.0",
        "runtime": {
            "sdk_version": runtime.sdk_version,
            "wheel_filename": runtime.wheel_filename,
            "wheel_sha256": runtime.wheel_sha256,
            "runtime_version": runtime.runtime_version,
            "runtime_sha256": runtime.runtime_sha256,
            "transport": runtime.transport,
            "auth_mode": runtime.auth_mode,
            "subscription_identity_sha256": runtime.subscription_identity_sha256,
        },
    }
    document["lock_sha256"] = lock_digest(document)
    return document


def test_model_lock_archives_catalog_and_preserves_previous_lock_on_drift(tmp_path) -> None:
    repository = LockRepository(tmp_path)
    source = archive()
    model_lock, _ = write_model_lock(
        repository,
        runtime_lock(),
        source,
        QualificationCaps(8, 8, 8, 8, 8),
        (qualification(),),
        QualificationUsage(8, 8, 8, 8, 8),
    )
    runtime = RuntimeIdentity(
        "1.0.8",
        "github_copilot_sdk-1.0.8-py3-none-any.whl",
        "7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa",
        "runtime-1",
        "a" * 64,
        "stdio",
        "copilot_subscription",
        "b" * 64,
    )

    verify_stage_locks(runtime_lock(), model_lock, runtime, source.catalog)
    changed = archive_native_catalog(b'{"models":[{"id":"native-b"}]}', {"models": []})
    with pytest.raises(ConformancePauseError, match="catalog bytes changed"):
        verify_stage_locks(runtime_lock(), model_lock, runtime, changed.catalog)
    assert repository.read("model-lock.json") == model_lock


def test_locks_reject_credentials_and_do_not_write_partial_documents(tmp_path) -> None:
    repository = LockRepository(tmp_path)
    with pytest.raises(ConformancePauseError, match="prohibited field"):
        repository.write("runtime-lock.json", {"access_token": "secret"})
    assert repository.read("runtime-lock.json") is None
