from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from memrelay_eval.adapters.copilot.client import (
    CopilotSdkClient,
    bootstrap_runtime,
    verify_sdk_lockfile,
)
from memrelay_eval.domain.errors import RuntimeLockError
from memrelay_eval.orchestration.control import LockRepository


class FakeModel:
    def to_dict(self) -> dict[str, object]:
        return {
            "id": "native-model",
            "family": "family",
            "capabilities": {
                "tools": True,
                "permissions": True,
                "context": 1,
                "events": True,
                "cancellation": True,
                "sessions": True,
            },
            "supported_reasoning_efforts": ["high"],
            "context_tier": "large",
        }


class FakeClient:
    started = 0
    stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def list_models(self) -> list[FakeModel]:
        return [FakeModel()]

    async def stop(self) -> None:
        self.stopped += 1


class FakeAuthStatus:
    def __init__(self, login: str | None) -> None:
        self.isAuthenticated = login is not None
        self.host = "https://github.com" if login else None
        self.login = login
        self.authType = "oauth" if login else None


class FakeAuthClient(FakeClient):
    def __init__(self, login: str | None) -> None:
        self._login = login

    async def get_auth_status(self) -> FakeAuthStatus:
        return FakeAuthStatus(self._login)


def test_official_sdk_adapter_archives_full_native_model_response_without_provider_routing() -> (
    None
):
    client = FakeClient()
    archive = asyncio.run(CopilotSdkClient(lambda: client).archive_models())

    assert client.started == client.stopped == 1
    assert archive.catalog.models[0].native_id == "native-model"
    assert b"native-model" in archive.raw_bytes


def test_sdk_lock_requires_only_the_frozen_wheel(
    evaluation_root: Path = Path(__file__).parents[3],
) -> None:
    verify_sdk_lockfile(evaluation_root / "uv.lock")
    bad_lock = evaluation_root / "tests" / "contract" / "copilot" / "bad-uv.lock"
    try:
        bad_lock.write_text('name = "github-copilot-sdk"\nversion = "1.0.9"\n', encoding="utf-8")
        with pytest.raises(RuntimeLockError, match="wheel"):
            verify_sdk_lockfile(bad_lock)
    finally:
        bad_lock.unlink(missing_ok=True)


def test_bootstrap_downloads_once_then_enables_implicit_download_guard(
    tmp_path: Path, evaluation_root: Path = Path(__file__).parents[3]
) -> None:
    runtime = tmp_path / "copilot.exe"
    runtime.write_bytes(b"runtime")
    calls: list[list[str]] = []
    repository = LockRepository(tmp_path / "locks")

    first = bootstrap_runtime(
        repository,
        evaluation_root / "uv.lock",
        command_runner=calls.append,
        runtime_locator=lambda: (runtime, "runtime-1"),
        subscription_subject=lambda: "signed-in-user",
        installed_version_provider=lambda _: "1.0.8",
    )
    second = bootstrap_runtime(
        repository,
        evaluation_root / "uv.lock",
        command_runner=calls.append,
        runtime_locator=lambda: (runtime, "runtime-2"),
        installed_version_provider=lambda _: "1.0.8",
    )

    assert len(calls) == 1
    assert first == second
    assert first["download_count"] == 1
    assert "signed-in-user" not in str(first)


def test_authenticated_subject_changes_with_the_authenticated_account() -> None:
    first = asyncio.run(
        CopilotSdkClient(lambda: FakeAuthClient("octocat")).authenticated_subscription_subject()
    )
    second = asyncio.run(
        CopilotSdkClient(lambda: FakeAuthClient("hubot")).authenticated_subscription_subject()
    )

    assert first != second
    assert "octocat" in first
    assert "hubot" in second


def test_missing_authenticated_subject_fails_closed() -> None:
    with pytest.raises(RuntimeLockError, match="identity is unavailable"):
        asyncio.run(
            CopilotSdkClient(lambda: FakeAuthClient(None)).authenticated_subscription_subject()
        )


def test_bootstrap_hashes_distinct_authenticated_subjects(
    tmp_path: Path, evaluation_root: Path = Path(__file__).parents[3]
) -> None:
    runtime = tmp_path / "copilot.exe"
    runtime.write_bytes(b"runtime")

    first = bootstrap_runtime(
        LockRepository(tmp_path / "first"),
        evaluation_root / "uv.lock",
        command_runner=lambda _: None,
        runtime_locator=lambda: (runtime, "runtime-1"),
        subscription_subject=lambda: "https://github.com\x1foctocat\x1foauth",
        installed_version_provider=lambda _: "1.0.8",
    )
    second = bootstrap_runtime(
        LockRepository(tmp_path / "second"),
        evaluation_root / "uv.lock",
        command_runner=lambda _: None,
        runtime_locator=lambda: (runtime, "runtime-1"),
        subscription_subject=lambda: "https://github.com\x1fhubot\x1foauth",
        installed_version_provider=lambda _: "1.0.8",
    )

    assert (
        first["runtime"]["subscription_identity_sha256"]
        != second["runtime"]["subscription_identity_sha256"]
    )


def test_copilot_adapter_has_no_forbidden_provider_route(
    evaluation_root: Path = Path(__file__).parents[3],
) -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(
            (evaluation_root / "src" / "memrelay_eval" / "adapters" / "copilot").glob("*.py")
        )
    ).lower()

    assert "openai" not in source
    assert "byok" not in source
    assert "providerconfig" not in source
