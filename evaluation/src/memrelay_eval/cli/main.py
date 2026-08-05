"""Command-line composition root for the isolated evaluator package."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from memrelay_eval import __version__
from memrelay_eval.cli.commands import run_stage, show_foundation_status


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = getattr(args, "handler", None)
    return 0 if handler is None else handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
