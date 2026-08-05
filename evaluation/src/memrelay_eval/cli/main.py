"""Command-line composition root for the isolated evaluator package."""

from __future__ import annotations

import argparse

from memrelay_eval import __version__
from memrelay_eval.cli.commands import bootstrap, lock_models, show_foundation_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memrelay-eval", description="memrelay evaluation tools")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command")
    foundation = subcommands.add_parser(
        "foundation", help="show current evaluator foundation status"
    )
    foundation.set_defaults(handler=show_foundation_status)
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    handler = getattr(args, "handler", None)
    return 0 if handler is None else handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
