"""Composition of durable adapters for the noninteractive reconciliation command."""

from __future__ import annotations

import os
from argparse import Namespace
from hashlib import sha256
from pathlib import Path

from memrelay_eval.adapters.artifacts.filesystem import FilesystemArtifactStore
from memrelay_eval.domain.errors import (
    ReconciliationError,
    TerminalDecisionConflictError,
)
from memrelay_eval.evidence.authority import ReconciliationAuthority
from memrelay_eval.evidence.manifest import reconciliation_command_manifest
from memrelay_eval.evidence.reconcile import ReconciliationService, load_reconciliation_input
from memrelay_eval.ledger.repository import SqliteLedger


def reconcile_stage_command(args: Namespace) -> int:
    """Reconcile a canonical terminal-evidence input without provider or analysis calls."""

    stage = str(args.stage)
    root = Path(args.artifacts_root)
    input_path = (
        Path(args.input)
        if args.input is not None
        else root / "reconciliation" / f"{stage}.input.json"
    )
    manifest_path = (
        Path(args.manifest)
        if args.manifest is not None
        else root / "reconciliation" / f"{stage}.command-manifest.json"
    )
    ledger_path = Path(args.ledger) if args.ledger is not None else root / "ledger.sqlite"
    input_hashes: dict[str, str] = {}
    output_hashes: dict[str, str] = {}
    runtime_lock_sha256: str | None = None
    protocol_sha256: str | None = None
    error_code: str | None = None
    exit_code = 0
    ledger: SqliteLedger | None = None

    try:
        raw_input = input_path.read_bytes()
        input_hashes["reconciliation_input"] = sha256(raw_input).hexdigest()
        request = load_reconciliation_input(raw_input)
        if request.matrix_key.stage != stage:
            raise ReconciliationError("reconciliation_stage_input_mismatch")
        runtime_lock_sha256 = request.runtime_lock_sha256
        protocol_sha256 = request.protocol_sha256
        store = FilesystemArtifactStore(root)
        ledger = SqliteLedger.open_control(ledger_path)
        authority = ledger.reconciliation_authority_for(request.run_id, request.attempt_id)
        if isinstance(authority, ReconciliationAuthority):
            input_hashes["required_evidence_matrix"] = authority.matrix_sha256
            input_hashes["required_evidence_producer_policy"] = authority.producer_policy_sha256
        result = ReconciliationService(store, ledger).reconcile(request)
        output_hashes = {
            "reconciliation_report": result.report_ref.sha256,
            "reconciliation_report_manifest": result.report_manifest_ref.sha256,
            "reconciliation_sha256": result.report.reconciliation_sha256,
            "inclusion_decision": result.decision.reconciliation_sha256,
        }
        if result.decision.status.value == "excluded":
            error_code = f"reconciliation_excluded_{result.decision.reason}"
            exit_code = 2
    except TerminalDecisionConflictError as error:
        error_code = error.code
        exit_code = 2
        if error.report_ref is not None:
            output_hashes["reconciliation_report"] = error.report_ref.sha256
        if error.report_manifest_ref is not None:
            output_hashes["reconciliation_report_manifest"] = error.report_manifest_ref.sha256
    except ReconciliationError as error:
        error_code = error.code
        exit_code = 2
    except (OSError, ValueError):
        error_code = "reconciliation_command_input_or_storage_failure"
        exit_code = 2
    finally:
        if ledger is not None:
            ledger.close()

    command_manifest = reconciliation_command_manifest(
        stage=stage,
        terminal_status="succeeded" if exit_code == 0 else "failed",
        exit_code=exit_code,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        runtime_lock_sha256=runtime_lock_sha256,
        protocol_sha256=protocol_sha256,
        error_code=error_code,
    )
    _write_command_manifest(manifest_path, command_manifest)
    print(command_manifest.decode("utf-8"))
    return exit_code


def _write_command_manifest(path: Path, payload: bytes) -> None:
    """Atomically persist a command result without using a process-global temp directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.staged")
    try:
        with staged.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    finally:
        if staged.exists():
            staged.unlink()
