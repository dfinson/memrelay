"""CLI command implementations."""

from __future__ import annotations

from argparse import Namespace


def show_foundation_status(_: Namespace) -> int:
    print("memrelay-eval foundation: unpaid conformance adapters only")
    return 0
