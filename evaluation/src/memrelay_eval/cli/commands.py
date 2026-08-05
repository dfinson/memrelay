"""CLI command implementations."""

from __future__ import annotations

from argparse import Namespace

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
