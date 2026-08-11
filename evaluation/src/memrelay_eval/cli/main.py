"""Command-line composition root for the isolated evaluator package."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from memrelay_eval import __version__
from memrelay_eval.cli.commands import (
    allocate_stochastic_rerun_command,
    analyze_stage,
    backup_terminal,
    bootstrap,
    compile_authored_catalog,
    lock_models,
    plan_offline_command,
    reconcile_stage,
    report_stage,
    reproduce_offline,
    run_stage,
    seal_reproduction_bundle_command,
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
    bootstrap_parser.add_argument(
        "--collector-archive",
        help="path to the already-downloaded frozen otelcol-contrib archive",
    )
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
    plan_offline = subcommands.add_parser(
        "plan-offline",
        help="deterministic offline catalog-to-planned-run dry run with no network or credentials",
    )
    plan_offline.add_argument(
        "--catalog",
        default="catalog/catalog.yaml",
        help="path to the authored YAML catalog",
    )
    plan_offline.add_argument(
        "--output-dir",
        default="catalog/generated",
        help="generated planning directory under the catalog root",
    )
    plan_offline.add_argument(
        "--lock",
        default=None,
        help="catalog lock path under the catalog root (defaults to catalog-lock.json)",
    )
    plan_offline.add_argument(
        "--manifest",
        default="catalog/plan-manifest.json",
        help="path for the atomically written redacted command manifest",
    )
    plan_offline.add_argument(
        "--prior-lock",
        help="prior valid catalog lock for version validation",
    )
    plan_offline.add_argument(
        "--runtime-lock",
        help="optional runtime lock referenced by name and hash only",
    )
    plan_offline.set_defaults(handler=plan_offline_command)
    reconcile = subcommands.add_parser(
        "reconcile",
        help="reconcile canonical terminal evidence and append one immutable inclusion decision",
    )
    reconcile.add_argument("--stage", required=True)
    reconcile.add_argument(
        "--input",
        help="canonical reconciliation input JSON; defaults under --artifacts-root by stage",
    )
    reconcile.add_argument(
        "--artifacts-root",
        default="artifacts",
        help="durable filesystem CAS root",
    )
    reconcile.add_argument(
        "--ledger",
        help="control-owned SQLite ledger path; defaults under --artifacts-root",
    )
    reconcile.add_argument(
        "--manifest",
        help="command manifest path; defaults under --artifacts-root by stage",
    )
    reconcile.set_defaults(handler=reconcile_stage)
    backup = subcommands.add_parser(
        "backup-terminal",
        help="snapshot and atomically publish terminal evidence to the configured second volume",
    )
    backup.add_argument("--backup-root", required=True)
    backup.add_argument("--artifacts-root", default="artifacts")
    backup.add_argument("--ledger", required=True)
    backup.add_argument("--run-id", required=True)
    backup.add_argument("--attempt-id", required=True)
    backup.set_defaults(handler=backup_terminal)
    analyze = subcommands.add_parser(
        "analyze",
        help="read one explicitly versioned reconciled Parquet dataset with a closed DuckDB API",
    )
    analyze.add_argument("--stage", required=True)
    analyze.add_argument("--parquet-root", required=True)
    analyze.add_argument("--dataset-version", required=True)
    analyze.add_argument(
        "--plan",
        required=True,
        help="canonical frozen analysis-plan JSON; arbitrary SQL is not accepted",
    )
    analyze.add_argument(
        "--output-root",
        required=True,
        help="derived-artifact root outside the immutable Parquet dataset root",
    )
    analyze.set_defaults(handler=analyze_stage)
    reproduce = subcommands.add_parser(
        "reproduce-offline",
        help="verify a sealed analysis/grader/evidence reproduction without credentials or network",
    )
    reproduce.add_argument("--bundle", required=True)
    reproduce.add_argument("--cas-root", required=True)
    reproduce.add_argument("--backup-root")
    reproduce.add_argument("--output-root", required=True)
    reproduce.set_defaults(handler=reproduce_offline)
    seal_reproduction = subcommands.add_parser(
        "seal-reproduction-bundle",
        help="seal retained analysis, grading, evidence, and runtime authorities",
    )
    seal_reproduction.add_argument("--parquet-root", required=True)
    seal_reproduction.add_argument("--dataset-version", required=True)
    seal_reproduction.add_argument("--queries", required=True)
    seal_reproduction.add_argument("--grader-result", required=True)
    seal_reproduction.add_argument("--normalized-evidence", required=True)
    seal_reproduction.add_argument("--protocol-sha256", required=True)
    seal_reproduction.add_argument("--runtime-lock", required=True)
    seal_reproduction.add_argument("--output-root", required=True)
    seal_reproduction.add_argument("--backup-receipt")
    seal_reproduction.set_defaults(handler=seal_reproduction_bundle_command)
    stochastic = subcommands.add_parser(
        "allocate-stochastic-rerun",
        help="allocate a separate non-confirmatory protocol/run/attempt identity",
    )
    stochastic.add_argument("--original-protocol-id", required=True)
    stochastic.add_argument("--original-run-id", required=True)
    stochastic.add_argument("--original-attempt-id", required=True)
    stochastic.add_argument(
        "--conclusion-class", choices=("null", "harm", "indeterminate", "positive"), required=True
    )
    stochastic.add_argument("--original-evidence-root", required=True)
    stochastic.add_argument("--output-root", required=True)
    stochastic.set_defaults(handler=allocate_stochastic_rerun_command)
    report = subcommands.add_parser(
        "report",
        help="render one immutable, local evidence-linked report without reanalysing inputs",
    )
    report.add_argument("--stage", required=True)
    report.add_argument(
        "--stage-evidence",
        required=True,
        help="canonical sealed analysis report input; mutable aliases are not accepted",
    )
    report.add_argument("--parquet-root", required=True)
    report.add_argument("--dataset-version", required=True)
    report.add_argument(
        "--output-root",
        default="artifacts",
        help="local artifact root; reports are appended below reports/<report-id>",
    )
    report.set_defaults(handler=report_stage)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = getattr(args, "handler", None)
    return 0 if handler is None else handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
