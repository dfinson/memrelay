"""Official SDK boundary that rejects alternate provider configuration."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from memrelay_eval.adapters.copilot.catalog import CatalogArchive, archive_native_catalog
from memrelay_eval.domain.entities import RuntimeIdentity
from memrelay_eval.domain.errors import ConformancePauseError, RuntimeLockError
from memrelay_eval.orchestration.control import LockRepository, lock_digest

SDK_VERSION = "1.0.8"
SDK_WHEEL = "github_copilot_sdk-1.0.8-py3-none-any.whl"
SDK_WHEEL_SHA256 = "7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa"


class CopilotSdkClient:
    """Minimal adapter over the official SDK; SDK objects never leave this module."""

    def __init__(self, client_factory: Callable[[], object] | None = None) -> None:
        self._client_factory = client_factory or _official_client_factory
        self._client: object | None = None

    async def archive_models(self) -> CatalogArchive:
        client = self._get_client()
        list_models = getattr(client, "list_models", None)
        if not callable(list_models):
            raise ConformancePauseError(
                "sdk_catalog_unsupported", "official SDK client does not expose list_models"
            )
        start = getattr(client, "start", None)
        stop = getattr(client, "stop", None)
        if not callable(start) or not callable(stop):
            raise ConformancePauseError(
                "sdk_lifecycle_unsupported", "official SDK client must expose start and stop"
            )
        await start()
        try:
            response = list_models()
            if hasattr(response, "__await__"):
                response = await response
            raw_bytes = _raw_response_bytes(response)
            return archive_native_catalog(raw_bytes, json.loads(raw_bytes))
        finally:
            await stop()

    async def authenticated_subscription_subject(self) -> str:
        """Return a stable non-secret subject from the runtime's auth status seam."""

        client = self._get_client()
        get_auth_status = getattr(client, "get_auth_status", None)
        start = getattr(client, "start", None)
        stop = getattr(client, "stop", None)
        if not callable(get_auth_status) or not callable(start) or not callable(stop):
            raise RuntimeLockError(
                "subscription_identity_unavailable",
                "official SDK client does not expose authenticated status",
            )
        await start()
        try:
            status = get_auth_status()
            if hasattr(status, "__await__"):
                status = await status
            return _subscription_subject_from_status(status)
        finally:
            await stop()

    def _get_client(self) -> object:
        if self._client is None:
            self._client = self._client_factory()
        return self._client


def _official_client_factory() -> object:
    try:
        module = importlib.import_module("copilot")
        factory = module.CopilotClient
    except (ImportError, AttributeError) as exc:
        raise ConformancePauseError(
            "sdk_unavailable", "github-copilot-sdk 1.0.8 is required for explicit live commands"
        ) from exc
    return factory()


def _raw_response_bytes(response: object) -> bytes:
    """Capture the complete SDK-native response without public-name inference."""

    if isinstance(response, bytes):
        return response
    raw_bytes = getattr(response, "raw_bytes", None)
    if isinstance(raw_bytes, bytes):
        return raw_bytes
    if isinstance(response, Mapping):
        raw = response.get("raw_bytes")
        if isinstance(raw, bytes):
            return raw
    if isinstance(response, list) and all(hasattr(item, "to_dict") for item in response):
        return json.dumps(
            {"models": [item.to_dict() for item in response]},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    raise ConformancePauseError("catalog_raw_missing", "SDK catalog response cannot be archived")


def verify_sdk_lockfile(lockfile: Path) -> None:
    """Require the single accepted distribution artifact before any SDK use."""

    text = lockfile.read_text(encoding="utf-8")
    package_start = text.find('name = "github-copilot-sdk"')
    if package_start < 0:
        raise RuntimeLockError(
            "sdk_lock_missing", "github-copilot-sdk is absent from the evaluator lock"
        )
    package_end = text.find("\n[[package]]", package_start)
    package = text[package_start:] if package_end < 0 else text[package_start:package_end]
    if (
        f'version = "{SDK_VERSION}"' not in package
        or SDK_WHEEL not in package
        or f"sha256:{SDK_WHEEL_SHA256}" not in package
        or "sdist =" in package
    ):
        raise RuntimeLockError(
            "sdk_wheel_contract_mismatch",
            "the evaluator lock must contain only the accepted Copilot SDK wheel",
        )


def bootstrap_runtime(
    lock_repository: LockRepository,
    lockfile: Path,
    command_runner: Callable[[list[str]], None] | None = None,
    runtime_locator: Callable[[], tuple[Path, str]] | None = None,
    subscription_subject: Callable[[], str] | None = None,
    installed_version_provider: Callable[[str], str] | None = None,
) -> dict[str, object]:
    """Download once, then bind all future executions to exact runtime bytes."""

    verify_sdk_lockfile(lockfile)
    existing = lock_repository.read("runtime-lock.json")
    if existing is not None:
        os.environ["COPILOT_SKIP_CLI_DOWNLOAD"] = "1"
        return existing
    runner = command_runner or _run_runtime_download
    runner([sys.executable, "-m", "copilot", "download-runtime"])
    locator = runtime_locator or _locate_runtime
    runtime_path, runtime_version = locator()
    if not runtime_path.is_file():
        raise RuntimeLockError(
            "runtime_missing", "SDK runtime download did not produce an executable"
        )
    installed_version = (installed_version_provider or importlib.metadata.version)(
        "github-copilot-sdk"
    )
    if installed_version != SDK_VERSION:
        raise RuntimeLockError(
            "sdk_version_mismatch", "installed Copilot SDK version does not match lock"
        )
    identity = RuntimeIdentity(
        sdk_version=installed_version,
        wheel_filename=SDK_WHEEL,
        wheel_sha256=SDK_WHEEL_SHA256,
        runtime_version=runtime_version,
        runtime_sha256=sha256(runtime_path.read_bytes()).hexdigest(),
        transport="stdio",
        auth_mode="copilot_subscription",
        subscription_identity_sha256=sha256(
            (subscription_subject or _current_subscription_subject)().encode("utf-8")
        ).hexdigest(),
    )
    document: dict[str, object] = {
        "schema_version": "1.0.0",
        "runtime": asdict(identity),
        "download_count": 1,
        "provenance": "explicit_local_bootstrap",
    }
    document["lock_sha256"] = lock_digest(document)
    lock_repository.write("runtime-lock.json", document)
    os.environ["COPILOT_SKIP_CLI_DOWNLOAD"] = "1"
    return document


def _run_runtime_download(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _locate_runtime() -> tuple[Path, str]:
    executable = shutil.which("copilot")
    if executable is None:
        cache_root = Path(os.environ.get("LOCALAPPDATA", "")) / "github-copilot-sdk" / "cli"
        candidates = sorted(cache_root.glob("*/copilot.exe"), key=lambda path: path.parent.name)
        if not candidates:
            raise RuntimeLockError("runtime_missing", "SDK runtime executable cannot be located")
        executable = str(candidates[-1])
    path = Path(executable)
    completed = subprocess.run([str(path), "--version"], check=True, capture_output=True, text=True)
    version = completed.stdout.strip().splitlines()[0]
    if not version:
        raise RuntimeLockError("runtime_version_missing", "SDK runtime returned no version")
    return path, version


def _current_subscription_subject() -> str:
    """Obtain identity from the current official SDK runtime session, never ambient state."""

    return _run_coroutine(CopilotSdkClient().authenticated_subscription_subject())


def _subscription_subject_from_status(status: object) -> str:
    authenticated = getattr(status, "isAuthenticated", None)
    host = getattr(status, "host", None)
    login = getattr(status, "login", None)
    auth_type = getattr(status, "authType", None)
    if (
        authenticated is not True
        or not isinstance(host, str)
        or not host
        or not isinstance(login, str)
        or not login
        or not isinstance(auth_type, str)
        or not auth_type
    ):
        raise RuntimeLockError(
            "subscription_identity_unavailable",
            "authenticated Copilot subscription identity is unavailable",
        )
    return f"{host}\x1f{login}\x1f{auth_type}"


def _run_coroutine(coroutine: Awaitable[str]) -> str:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    coroutine.close()
    raise RuntimeLockError(
        "subscription_identity_unavailable",
        "subscription identity must be resolved outside an active event loop",
    )
