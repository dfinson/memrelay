"""CLI command implementations."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from memrelay_eval.catalog.validation import CatalogValidationError, validate_catalog
from memrelay_eval.domain.errors import CrossRepositoryDeniedError
from memrelay_eval.orchestration.stages import refuse_cross_repository_stage


def show_foundation_status(_: Namespace) -> int:
    print("memrelay-eval foundation: unpaid conformance adapters only")
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


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value is not None else None
