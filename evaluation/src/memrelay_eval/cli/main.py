"""Command-line composition root for the isolated evaluator package."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from memrelay_eval import __version__
from memrelay_eval.cli.commands import (
    bootstrap,
    compile_authored_catalog,
    lock_models,
    run_stage,
    show_effective_configuration,
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
    effective_config = subcommands.add_parser(
        "effective-config",
        help="resolve explicit configuration and print a redacted canonical projection",
    )
    effective_config.add_argument("--config", help="evaluator TOML configuration file")
    effective_config.add_argument("--stage")
    effective_config.add_argument("--timeout-seconds", type=int)
    effective_config.add_argument("--max-concurrency", type=int)
    effective_config.add_argument(
        "--network-policy",
        help="JSON object for the explicit CLI network policy",
    )
    effective_config.add_argument(
        "--credential-reference",
        action="append",
        metavar="VARIABLE:PROCESS",
        help="named credential variable and exact target process; never a credential value",
    )
    effective_config.set_defaults(handler=show_effective_configuration)
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
    compile_catalog = subcommands.add_parser(
        "compile-catalog",
        help="validate and atomically publish canonical offline catalog artifacts",
    )
    compile_catalog.add_argument(
        "--catalog",
        default="catalog/catalog.yaml",
        help="path to the authored YAML catalog",
    )
    compile_catalog.add_argument(
        "--output-dir",
        default="catalog/generated",
        help="generated catalog directory directly under the catalog root",
    )
    compile_catalog.add_argument(
        "--lock",
        default="catalog/catalog-lock.json",
        help="catalog lock directly under the catalog root",
    )
    compile_catalog.add_argument(
        "--manifest",
        default="catalog/compile-manifest.json",
        help="redacted command manifest path",
    )
    compile_catalog.add_argument(
        "--prior-lock",
        help="prior valid catalog lock used only for semantic version validation",
    )
    compile_catalog.add_argument(
        "--runtime-lock",
        help="optional runtime lock referenced by name and hash only",
    )
    compile_catalog.set_defaults(handler=compile_authored_catalog)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = getattr(args, "handler", None)
    return 0 if handler is None else handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
