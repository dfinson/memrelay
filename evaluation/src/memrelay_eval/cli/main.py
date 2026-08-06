"""Command-line composition root for the isolated evaluator package."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from memrelay_eval import __version__
from memrelay_eval.cli.commands import (
    bootstrap,
    lock_models,
    run_stage,
    show_foundation_status,
    validate_authored_catalog,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memrelay-eval", description="memrelay evaluation tools")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command")
    foundation = subcommands.add_parser(
        "foundation", help="show current evaluator foundation status"
    )
    foundation.set_defaults(handler=show_foundation_status)
    run = subcommands.add_parser("run", help="request a recognized evaluator stage")
    run.add_argument("--stage", choices=("cross-repo",), required=True)
    run.set_defaults(handler=run_stage)
    bootstrap_parser = subcommands.add_parser(
        "bootstrap", help="explicitly verify and lock the official Copilot runtime"
    )
    bootstrap_parser.add_argument("--backup-root", required=True)
    bootstrap_parser.set_defaults(handler=bootstrap)
    lock_models_parser = subcommands.add_parser(
        "lock-models", help="explicitly qualify and lock native Copilot models"
    )
    lock_models_parser.add_argument("--credit-cap", type=float, required=True)
    lock_models_parser.add_argument("--token-cap", type=int, required=True)
    lock_models_parser.add_argument("--active-seconds-cap", type=float, required=True)
    lock_models_parser.add_argument("--wall-seconds-cap", type=float, required=True)
    lock_models_parser.set_defaults(handler=lock_models)
    validate_catalog = subcommands.add_parser(
        "validate-catalog",
        help="validate authored catalog YAML without generating execution artifacts",
    )
    validate_catalog.add_argument(
        "--catalog",
        default="catalog/catalog.yaml",
        help="path to the authored YAML catalog",
    )
    validate_catalog.add_argument(
        "--prior-lock",
        help="prior valid catalog lock used only for semantic version validation",
    )
    validate_catalog.set_defaults(handler=validate_authored_catalog)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = getattr(args, "handler", None)
    return 0 if handler is None else handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
