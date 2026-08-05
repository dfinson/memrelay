"""CLI command implementations."""

from __future__ import annotations

import asyncio
from argparse import Namespace
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from memrelay_eval.adapters.copilot.catalog import eligible_models
from memrelay_eval.adapters.copilot.client import CopilotSdkClient, bootstrap_runtime
from memrelay_eval.adapters.copilot.session import qualify_native_catalog
from memrelay_eval.domain.entities import QualificationCaps
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.orchestration.control import (
    LockRepository,
    reuse_or_reject_model_lock,
    write_model_lock,
)


def show_foundation_status(_: Namespace) -> int:
    print("memrelay-eval foundation: unpaid conformance adapters only")
    return 0


def bootstrap(args: Namespace) -> int:
    """Perform the one permitted runtime download after checking the evidence root."""

    backup_root = Path(args.backup_root).expanduser().resolve()
    if not backup_root.exists():
        raise ConformancePauseError(
            "backup_root_missing", "backup root must exist before bootstrap"
        )
    if backup_root.drive == Path.cwd().resolve().drive:
        raise ConformancePauseError(
            "backup_root_not_second_volume",
            "backup root must be on a different volume before a live runtime bootstrap",
        )
    evaluation_root = Path(__file__).parents[3]
    repository = LockRepository(evaluation_root / "artifacts")
    bootstrap_runtime(repository, evaluation_root / "uv.lock")
    print("Copilot runtime lock written; future implicit runtime downloads are disabled")
    return 0


def lock_models(
    args: Namespace,
    *,
    repository: LockRepository | None = None,
    archive_models: Callable[[], Awaitable[Any]] | None = None,
    qualify: Callable[
        [Any, QualificationCaps], Awaitable[tuple[tuple[Any, ...], Any]]
    ] = qualify_native_catalog,
) -> int:
    """Run exactly eight arm-blind nonstudy sessions per eligible native model."""

    evaluation_root = Path(__file__).parents[3]
    repository = repository or LockRepository(evaluation_root / "artifacts")
    runtime_lock = repository.read("runtime-lock.json")
    if runtime_lock is None:
        raise ConformancePauseError(
            "runtime_lock_missing",
            "bootstrap must produce a runtime lock before model qualification",
        )
    existing = reuse_or_reject_model_lock(
        repository,
        runtime_lock,
        credit_limit=args.credit_cap,
        token_limit=args.token_cap,
        active_seconds_limit=args.active_seconds_cap,
        wall_seconds_limit=args.wall_seconds_cap,
    )
    if existing is not None:
        print("Existing native model lock verified; no qualification sessions were run")
        return 0
    if archive_models is None:
        archive_models = CopilotSdkClient().archive_models
    archive = asyncio.run(archive_models())
    eligible_count = len(eligible_models(archive.catalog))
    if eligible_count == 0:
        raise ConformancePauseError(
            "no_eligible_models", "native catalog has no qualified model candidates"
        )
    caps = QualificationCaps(
        session_limit=eligible_count * 8,
        credit_limit=args.credit_cap,
        token_limit=args.token_cap,
        active_seconds_limit=args.active_seconds_cap,
        wall_seconds_limit=args.wall_seconds_cap,
    )
    qualifications, consumption = asyncio.run(qualify(archive.catalog, caps))
    write_model_lock(repository, runtime_lock, archive, caps, qualifications, consumption)
    print("Native model catalog and qualification lock written")
    return 0
