"""CLI command implementations."""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from memrelay_eval.application.copilot_services import (
    CopilotSdkClient,
    bootstrap_runtime,
    eligible_models,
    qualify_native_catalog,
)
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.catalog.compiler import compile_catalog_command
from memrelay_eval.catalog.validation import CatalogValidationError, validate_catalog
from memrelay_eval.domain.entities import QualificationCaps
from memrelay_eval.domain.errors import (
    ConformancePauseError,
    CrossRepositoryDeniedError,
    InvalidConfigurationError,
)
from memrelay_eval.orchestration.configuration import (
    load_evaluator_toml,
    resolve_effective_configuration,
)
from memrelay_eval.orchestration.control import (
    LockRepository,
    reuse_or_reject_model_lock,
    write_model_lock,
)
from memrelay_eval.orchestration.stages import refuse_cross_repository_stage


def show_foundation_status(_: Namespace) -> int:
    print("memrelay-eval foundation: unpaid conformance adapters only")
    return 0


def show_effective_configuration(args: Namespace) -> int:
    """Resolve explicit sources and print only the redacted canonical projection."""
    evaluator_file = load_evaluator_toml(Path(args.config)) if args.config is not None else {}
    cli: dict[str, object] = {
        "stage": args.stage,
        "timeout_seconds": args.timeout_seconds,
        "max_concurrency": args.max_concurrency,
    }
    if args.network_policy is not None:
        try:
            cli["network_policy"] = json.loads(args.network_policy)
        except json.JSONDecodeError as error:
            raise InvalidConfigurationError() from error
    if args.credential_reference:
        cli["credential_references"] = [
            {"variable_name": variable, "target_process": process}
            for variable, process in (
                _split_credential_reference(value) for value in args.credential_reference
            )
        ]
    configuration = resolve_effective_configuration(cli=cli, evaluator_file=evaluator_file)
    print(canonical_bytes(configuration.to_document()).decode("utf-8"))
    return 0


def run_stage(args: Namespace) -> int:
    """Handle the only currently recognized execution-stage request."""

    if args.stage != "cross-repo":
        raise ValueError("unsupported evaluator stage")
    try:
        refuse_cross_repository_stage()
    except CrossRepositoryDeniedError as error:
        print(f"execution denied: {error.reason}")
        return 2
    raise AssertionError("cross-repository execution must remain unavailable in evaluator v1")


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


def validate_authored_catalog(args: Namespace) -> int:
    """Validate source YAML without compiling tasks, emitting a lock, or using a provider."""

    try:
        result = validate_catalog(Path(args.catalog), prior_lock=_optional_path(args.prior_lock))
    except CatalogValidationError as error:
        for diagnostic in error.diagnostics:
            print(diagnostic)
        return 1
    print(f"{result.source_path}: valid ({result.change_kind} change)")
    return 0


def compile_authored_catalog(args: Namespace) -> int:
    """Compile a validated catalog without provider, fixture-content, or eligibility work."""

    result = compile_catalog_command(
        Path(args.catalog),
        output_dir=Path(args.output_dir),
        lock_path=Path(args.lock),
        manifest_path=Path(args.manifest),
        prior_lock=_optional_path(args.prior_lock),
        runtime_lock=_optional_path(args.runtime_lock),
    )
    if isinstance(result.error, CatalogValidationError):
        for diagnostic in result.error.diagnostics:
            print(diagnostic)
    elif result.error is not None:
        print(result.error)
    if result.terminal_status == "succeeded":
        print("catalog compiled: canonical unpaid-conformance artifacts published")
    return result.exit_code


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value is not None else None


def _split_credential_reference(value: str) -> tuple[str, str]:
    variable, separator, process = value.partition(":")
    if not separator or not variable or not process:
        raise InvalidConfigurationError()
    return variable, process
