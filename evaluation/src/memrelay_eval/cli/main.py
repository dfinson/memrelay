"""Command-line composition root for the isolated evaluator package."""

from __future__ import annotations

import argparse

from memrelay_eval import __version__
from memrelay_eval.cli.commands import show_foundation_status, validate_authored_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memrelay-eval", description="memrelay evaluation tools")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command")
    foundation = subcommands.add_parser(
        "foundation", help="show current evaluator foundation status"
    )
    foundation.set_defaults(handler=show_foundation_status)
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


def main() -> int:
    args = build_parser().parse_args()
    handler = getattr(args, "handler", None)
    return 0 if handler is None else handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
