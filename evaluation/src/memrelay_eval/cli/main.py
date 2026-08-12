"""Command-line composition root for the isolated evaluator package."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from pathlib import Path

from memrelay_eval import __version__
from memrelay_eval.canonical import canonical_digest
from memrelay_eval.cli.commands import (
    allocate_stochastic_rerun_command,
    analyze_stage,
    backup_terminal,
    bootstrap,
    compile_authored_catalog,
    conformance,
    gate_pilot,
    lock_models,
    observation_conformance,
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
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.evidence.manifest import stage_command_manifest


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
    run.add_argument(
        "--stage",
        choices=("integration", "pilot", "primary", "secondary", "cross-repo"),
        required=True,
    )
    run.add_argument(
        "--entry-bundle",
        dest="entry_bundle",
        help="path to the sealed stage entry bundle for this stage",
    )
    run.add_argument(
        "--predecessor-exit",
        dest="predecessor_exit",
        help="path to the sealed accepted predecessor exit bundle",
    )
    run.add_argument(
        "--authorization",
        dest="authorization",
        help="path to the independent sealed stage authorization",
    )
    run.add_argument(
        "--conformance-report",
        dest="conformance_report",
        help="path to the immutable passed conformance report required before enrollment",
    )
    run.add_argument(
        "--bootstrap-receipt",
        dest="bootstrap_receipt",
        help="path to the immutable environment-bound bootstrap receipt",
    )
    run.add_argument(
        "--pilot-plan",
        dest="pilot_plan",
        help="sealed 128-unit blinded pilot plan; required when --stage pilot",
    )
    run.add_argument(
        "--output-root",
        dest="output_root",
        default="artifacts",
        help="root under which the append-only command manifest is written",
    )
    run.add_argument(
        "--task-plan",
        help="canonical JSON task-family plan required to materialize primary or secondary units",
    )
    run.add_argument(
        "--limits",
        help="canonical JSON sealed paid-limit document required for primary or secondary",
    )
    run.add_argument(
        "--primary-plan",
        help="immutable primary plan required by a secondary request",
    )
    run.add_argument(
        "--primary-conclusion",
        help="immutable reconciled primary conclusion required by a secondary request",
    )
    run.add_argument(
        "--model-lock",
        help="verified Story 2.1 native model qualification lock required by secondary",
    )
    run.add_argument(
        "--secondary-entry",
        action="append",
        metavar="ROLE:PATH",
        help="one M1/M2 secondary entry bundle; repeat for each qualified role",
    )
    run.add_argument(
        "--secondary-authorization",
        action="append",
        metavar="ROLE:PATH",
        help="matching M1/M2 secondary authorization; repeat for each qualified role",
    )
    run.set_defaults(handler=run_stage)
    pilot_gate = subcommands.add_parser(
        "pilot-gate",
        help="seal a non-confirmatory blinded pilot exit from frozen evidence",
    )
    pilot_gate.add_argument("--pilot-plan", required=True)
    pilot_gate.add_argument("--exit-evidence", required=True)
    pilot_gate.add_argument("--output-root", default="artifacts")
    _add_command_manifest_root(pilot_gate)
    pilot_gate.set_defaults(handler=gate_pilot, stage="pilot")
    bootstrap_parser = subcommands.add_parser(
        "bootstrap", help="explicitly verify and lock the official Copilot runtime"
    )
    bootstrap_parser.add_argument("--backup-root", required=True)
    bootstrap_parser.add_argument(
        "--collector-archive",
        help="path to the already-downloaded frozen otelcol-contrib archive",
    )
    bootstrap_parser.add_argument(
        "--mode",
        choices=("unpaid_ci", "provider_qualification"),
        default="provider_qualification",
    )
    bootstrap_parser.add_argument("--environment-sha256")
    bootstrap_parser.add_argument("--protocol-sha256")
    _add_command_manifest_root(bootstrap_parser)
    bootstrap_parser.set_defaults(handler=bootstrap)
    conformance_parser = subcommands.add_parser(
        "conformance",
        help="run explicit unpaid CI or provider-qualification conformance",
    )
    conformance_parser.add_argument(
        "--catalog",
        default="catalog/catalog.yaml",
        help="synthetic catalog used for the unpaid catalog-to-report path",
    )
    conformance_parser.add_argument(
        "--stage-locks",
        required=True,
        help="canonical JSON map of the frozen integration-entry hashes",
    )
    conformance_parser.add_argument(
        "--bootstrap-receipt",
        required=True,
        help="immutable bootstrap receipt bound to this runtime and environment",
    )
    conformance_parser.add_argument(
        "--mode",
        choices=("unpaid_ci", "provider_qualification"),
        default="unpaid_ci",
    )
    conformance_parser.add_argument(
        "--entry-bundle",
        help="sealed Story 6.1 entry bundle required for provider qualification",
    )
    conformance_parser.add_argument(
        "--authorization",
        help="independent paid Story 6.1 authorization required for provider qualification",
    )
    conformance_parser.add_argument(
        "--output-root",
        default="artifacts",
        help="append-only root for immutable conformance reports",
    )
    _add_command_manifest_root(conformance_parser)
    conformance_parser.set_defaults(handler=conformance)
    lock_models_parser = subcommands.add_parser(
        "lock-models", help="explicitly qualify and lock native Copilot models"
    )
    lock_models_parser.add_argument("--credit-cap", type=float, required=True)
    lock_models_parser.add_argument("--token-cap", type=int, required=True)
    lock_models_parser.add_argument("--active-seconds-cap", type=float, required=True)
    lock_models_parser.add_argument("--wall-seconds-cap", type=float, required=True)
    _add_command_manifest_root(lock_models_parser)
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
    _add_command_manifest_root(validate_catalog)
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
    _add_command_manifest_root(compile_catalog)
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
    _add_command_manifest_root(plan_offline)
    plan_offline.set_defaults(handler=plan_offline_command)
    observation = subcommands.add_parser(
        "observation-conformance",
        help="qualify one frozen replay or file-watch sentinel evidence input",
    )
    observation.add_argument(
        "--input",
        required=True,
        help="canonical prior contract identity request; native evidence is not accepted",
    )
    observation.add_argument(
        "--product-config",
        required=True,
        help="current product TOML configuration selecting replay or file_watch",
    )
    observation.add_argument(
        "--runtime-lock",
        required=True,
        help="current runtime lock whose bytes are bound before sentinel injection",
    )
    observation.add_argument(
        "--sentinel-count",
        type=int,
        default=3,
        help="positive number of fresh synthetic sentinels injected for this execution",
    )
    observation.add_argument(
        "--window-seconds",
        type=int,
        default=30,
        help="positive frozen conformance-window duration created immediately before injection",
    )
    observation.add_argument(
        "--output-root",
        default="artifacts",
        help="append-only artifact root for path-scoped decisions and manifests",
    )
    observation.add_argument(
        "--fault-injection",
        action="append",
        default=[],
        help=argparse.SUPPRESS,
    )
    _add_command_manifest_root(observation)
    observation.set_defaults(handler=observation_conformance)
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
    _add_command_manifest_root(reconcile)
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
    _add_command_manifest_root(backup)
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
    _add_command_manifest_root(analyze)
    analyze.set_defaults(handler=analyze_stage)
    reproduce = subcommands.add_parser(
        "reproduce-offline",
        help="verify a sealed analysis/grader/evidence reproduction without credentials or network",
    )
    reproduce.add_argument("--bundle", required=True)
    reproduce.add_argument("--cas-root", required=True)
    reproduce.add_argument("--backup-root")
    reproduce.add_argument("--output-root", required=True)
    _add_command_manifest_root(reproduce)
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
    _add_command_manifest_root(seal_reproduction)
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
    _add_command_manifest_root(stochastic)
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
    _add_command_manifest_root(report)
    report.set_defaults(handler=report_stage)
    return parser


def _add_command_manifest_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--command-manifest-root",
        help="append-only root for the standard terminal command manifest",
    )


_LEGACY_MANIFESTED_COMMANDS = frozenset(
    {
        "bootstrap",
        "conformance",
        "lock-models",
        "validate-catalog",
        "compile-catalog",
        "plan-offline",
        "observation-conformance",
        "reconcile",
        "backup-terminal",
        "analyze",
        "reproduce-offline",
        "seal-reproduction-bundle",
        "allocate-stochastic-rerun",
        "report",
        "pilot-gate",
    }
)

_INPUT_PATH_FIELDS = {
    "bootstrap": ("collector_archive",),
    "conformance": ("catalog", "stage_locks", "bootstrap_receipt", "entry_bundle", "authorization"),
    "lock-models": (),
    "validate-catalog": ("catalog", "prior_lock"),
    "compile-catalog": ("catalog", "prior_lock", "runtime_lock"),
    "plan-offline": ("catalog", "prior_lock", "runtime_lock", "lock"),
    "observation-conformance": ("input", "product_config", "runtime_lock"),
    "reconcile": ("input", "ledger"),
    "backup-terminal": ("artifacts_root", "ledger"),
    "analyze": ("plan", "parquet_root"),
    "reproduce-offline": ("bundle", "cas_root", "backup_root"),
    "seal-reproduction-bundle": (
        "parquet_root",
        "queries",
        "grader_result",
        "normalized_evidence",
        "runtime_lock",
        "backup_receipt",
    ),
    "allocate-stochastic-rerun": ("original_evidence_root",),
    "report": ("stage_evidence", "parquet_root"),
    "pilot-gate": ("pilot_plan", "exit_evidence"),
}

_OUTPUT_PATH_FIELDS = {
    "bootstrap": (),
    "conformance": ("output_root",),
    "lock-models": (),
    "validate-catalog": (),
    "compile-catalog": ("output_dir", "lock", "manifest"),
    "plan-offline": ("output_dir", "manifest"),
    "observation-conformance": ("output_root",),
    "reconcile": ("artifacts_root", "manifest"),
    "backup-terminal": ("backup_root",),
    "analyze": ("output_root",),
    "reproduce-offline": ("output_root",),
    "seal-reproduction-bundle": ("output_root",),
    "allocate-stochastic-rerun": ("output_root",),
    "report": ("output_root",),
    "pilot-gate": ("output_root",),
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        return 0
    if args.command == "run" and args.stage == "cross-repo":
        # Story 7.3 must deny before artifact-root discovery or manifest publication.
        return handler(args)
    if args.command not in _LEGACY_MANIFESTED_COMMANDS:
        return handler(args)
    return _invoke_with_command_manifest(args, handler)


def _invoke_with_command_manifest(
    args: argparse.Namespace, handler: Callable[[argparse.Namespace], int]
) -> int:
    """Execute one legacy CLI command and append its uniform terminal authority."""

    command = args.command
    input_hashes: dict[str, str] = {}
    terminal_status = "succeeded"
    exit_code = 0
    error_code: str | None = None
    error: BaseException | None = None
    try:
        input_hashes = _path_hashes(args, _INPUT_PATH_FIELDS[command])
        exit_code = handler(args)
        if exit_code != 0:
            terminal_status = "failed"
            error_code = f"{command.replace('-', '_')}_nonzero_exit"
    except KeyboardInterrupt as caught:
        terminal_status = "interrupted"
        exit_code = 130
        error_code = "keyboard_interrupt"
        error = caught
    except Exception as caught:
        terminal_status = "failed"
        exit_code = 2
        error_code = _error_code(caught)
        error = caught

    try:
        output_hashes = _path_hashes(args, _OUTPUT_PATH_FIELDS[command])
        if command in {"bootstrap", "lock-models"}:
            artifact_root = _lock_artifact_root()
            if artifact_root.exists():
                output_hashes["artifact_root"] = _hash_path(artifact_root)
        runtime_lock_sha256, protocol_sha256 = _authority_hashes(args)
    except Exception as caught:
        output_hashes = {}
        runtime_lock_sha256 = None
        protocol_sha256 = None
        if error is None:
            terminal_status = "failed"
            exit_code = 2
            error_code = _error_code(caught)
            error = caught
    manifest = stage_command_manifest(
        command=command,
        stage=str(getattr(args, "stage", "conformance")),
        terminal_status=terminal_status,
        exit_code=exit_code,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        runtime_lock_sha256=runtime_lock_sha256,
        protocol_sha256=protocol_sha256,
        error_code=error_code,
    )
    digest = json.loads(manifest.decode("utf-8"))["digest"]
    manifest_path = _command_manifest_root(args) / "commands" / f"{command}-{digest}.json"
    original_error_code = error_code if error is not None else None
    publication_failure: StageControlError | None = None
    emitted_manifest = manifest
    try:
        _write_immutable_manifest(manifest_path, manifest)
    except StageControlError as publication_error:
        emitted_manifest = stage_command_manifest(
            command=command,
            stage=str(getattr(args, "stage", "conformance")),
            terminal_status="failed",
            exit_code=2,
            input_hashes=input_hashes,
            output_hashes=output_hashes,
            runtime_lock_sha256=runtime_lock_sha256,
            protocol_sha256=protocol_sha256,
            error_code=publication_error.code,
            prior_error_code=original_error_code,
        )
        publication_failure = publication_error
    finally:
        if publication_failure is not None:
            print(emitted_manifest.decode("utf-8"))
    if publication_failure is not None:
        evidence = (str(manifest_path),)
        if original_error_code is not None:
            evidence += (original_error_code,)
        raise StageControlError(publication_failure.code, evidence) from publication_failure
    if error is not None:
        raise error
    return exit_code


def _command_manifest_root(args: argparse.Namespace) -> Path:
    explicit = getattr(args, "command_manifest_root", None)
    if explicit:
        return Path(explicit)
    for attribute in ("output_root", "artifacts_root"):
        value = getattr(args, attribute, None)
        if value:
            return Path(value)
    manifest = getattr(args, "manifest", None)
    if manifest:
        return Path(manifest).parent
    return Path("artifacts")


def _path_hashes(args: argparse.Namespace, fields: tuple[str, ...]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for field in fields:
        path = _command_path(args, field)
        if path is not None and path.exists():
            hashes[field] = _hash_path(path)
    return hashes


def _command_path(args: argparse.Namespace, field: str) -> Path | None:
    value = getattr(args, field, None)
    if value:
        return Path(value)
    if args.command == "reconcile":
        root = Path(args.artifacts_root)
        if field == "input":
            return root / "reconciliation" / f"{args.stage}.input.json"
        if field == "ledger":
            return root / "ledger.sqlite"
    return None


def _hash_path(path: Path) -> str:
    if path.is_file():
        return sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        raise ValueError(f"manifest path is neither file nor directory: {path}")
    records: list[dict[str, str]] = []
    for child in sorted(path.rglob("*")):
        if not child.is_file() or "commands" in child.relative_to(path).parts:
            continue
        records.append(
            {
                "path": child.relative_to(path).as_posix(),
                "sha256": sha256(child.read_bytes()).hexdigest(),
            }
        )
    return canonical_digest({"entries": records})


def _authority_hashes(args: argparse.Namespace) -> tuple[str | None, str | None]:
    runtime_lock = getattr(args, "runtime_lock", None)
    runtime_lock_sha256 = getattr(args, "runtime_lock_sha256", None)
    if runtime_lock_sha256 is None:
        runtime_lock_sha256 = (
            _hash_path(Path(runtime_lock))
            if runtime_lock and Path(runtime_lock).is_file()
            else None
        )
    protocol_sha256 = getattr(args, "protocol_sha256", None)
    for field in _INPUT_PATH_FIELDS.get(args.command, ()):
        path = _command_path(args, field)
        if path is None:
            continue
        documents = (
            (_json_document(path),)
            if path.is_file()
            else _dataset_manifest_documents(path, getattr(args, "dataset_version", None))
        )
        for document in documents:
            if document is None:
                continue
            runtime_lock_sha256 = runtime_lock_sha256 or _find_hash(document, "runtime_lock_sha256")
            protocol_sha256 = protocol_sha256 or _find_hash(document, "protocol_sha256")
    if runtime_lock_sha256 is None:
        root = (
            _lock_artifact_root()
            if args.command in {"bootstrap", "lock-models"}
            else _command_manifest_root(args)
        )
        runtime_lock_sha256 = _runtime_lock_from_output_root(root)
    return runtime_lock_sha256, protocol_sha256


def _lock_artifact_root() -> Path:
    return Path(__file__).parents[3] / "artifacts"


def _dataset_manifest_documents(path: Path, dataset_version: object) -> tuple[object | None, ...]:
    if not path.is_dir():
        return ()
    candidates = (
        (path / str(dataset_version) / "dataset-manifest.json",)
        if dataset_version
        else tuple(path.glob("**/dataset-manifest.json"))
    )
    return tuple(_json_document(candidate) for candidate in candidates)


def _runtime_lock_from_output_root(root: Path) -> str | None:
    runtime_lock = root / "runtime-lock.json"
    return _hash_path(runtime_lock) if runtime_lock.is_file() else None


def _json_document(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _find_hash(document: object, key: str) -> str | None:
    if isinstance(document, Mapping):
        value = document.get(key)
        if isinstance(value, str) and len(value) == 64:
            return value
        for child in document.values():
            found = _find_hash(child, key)
            if found is not None:
                return found
    elif isinstance(document, list):
        for child in document:
            found = _find_hash(child, key)
            if found is not None:
                return found
    return None


def _error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and code else "command_terminal_failure"


def _write_immutable_manifest(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.is_file() and path.read_bytes() == data:
                return
            raise StageControlError("command_manifest_conflict", (str(path),))
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            os.link(temporary, path)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != data:
                raise StageControlError("command_manifest_conflict", (str(path),)) from None
        finally:
            if temporary.exists():
                temporary.unlink()
        if not path.is_file() or path.read_bytes() != data:
            raise StageControlError("command_manifest_publish_failed", (str(path),))
    except StageControlError:
        raise
    except OSError as error:
        raise StageControlError("command_manifest_publish_failed", (str(path),)) from error


if __name__ == "__main__":
    raise SystemExit(main())
