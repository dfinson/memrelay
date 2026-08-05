"""CLI command implementations."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from memrelay_eval.catalog.validation import CatalogValidationError, validate_catalog


def show_foundation_status(_: Namespace) -> int:
    print("memrelay-eval foundation: unpaid conformance adapters only")
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


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value is not None else None
