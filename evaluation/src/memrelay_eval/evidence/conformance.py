"""Immutable bootstrap and conformance evidence for the evaluator gate."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from memrelay_eval.canonical import (
    CanonicalizationError,
    attach_digest,
    canonical_bytes,
    verify_digest,
)
from memrelay_eval.domain.errors import ConformancePauseError as _ConformancePauseError
from memrelay_eval.domain.errors import StageControlError

CONFORMANCE_SCHEMA_VERSION = "1.0.0"
CONFORMANCE_LABEL = "conformance_not_efficacy_evidence"
_SHA256_LENGTH = 64


def ConformancePauseError(
    code: str, message: str | None = None, evidence: tuple[str, ...] = ()
) -> _ConformancePauseError:
    """Create a typed pause without duplicating its code as a user-facing message."""

    return _ConformancePauseError(code, message or code.replace("_", " "), evidence)


# These stable proof identifiers are the architecture's mandatory catalog-to-report
# closure. A report is valid only when it contains every proof exactly once.
REQUIRED_PROOF_IDS = frozenset(
    {
        "CATALOG-SCHEMA",
        "CATALOG-HASHES",
        "CATALOG-MAPPINGS",
        "CATALOG-CI",
        "MODEL-CATALOG-SNAPSHOT",
        "MODEL-SELECTION-PIN",
        "AUTH-COPILOT-SUBSCRIPTION",
        "AUTH-SDK-BYOK-DENY",
        "SECRET-OPENAI-ISOLATION",
        "FRAMEWORK-ROUTE-FAIL-CLOSED",
        "ARM-PARITY",
        "TEL-PROVIDER-IDENTITY",
        "COST-PROVIDER-LEDGERS",
        "TOOL-INSPECT-CONFORMANCE",
        "TOOL-OTEL-CONFORMANCE",
        "TOOL-OPENINFERENCE-CONFORMANCE",
        "TOOL-PARQUET-CONFORMANCE",
        "WORKSPACE-TEMPORARY-WORKTREE",
        "WORKSPACE-ISOLATED-CLONE",
        "GRADER-CONTRACT",
        "JUDGE-BLINDING",
        "CAS-CORRUPTION",
        "BACKUP-RESTORE-SECOND-VOLUME",
        "RECONCILIATION-COMPLETENESS",
        "REPLAY-SEALED",
        "REPORT-EVIDENCE-LINK",
        "CROSS-REPOSITORY-DENY",
    }
)

REQUIRED_STAGE_LOCKS = frozenset(
    {
        "catalog_sha256",
        "protocol_sha256",
        "sdk_sha256",
        "runtime_lock_sha256",
        "model_lock_sha256",
        "environment_sha256",
        "grader_sha256",
        "judge_sha256",
        "telemetry_sha256",
        "price_table_sha256",
        "limits_sha256",
        "preceding_exit_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class ProofReceipt:
    """One immutable, outcome-blind proof result."""

    proof_id: str
    status: str
    implementation_sha256: str
    input_sha256: str
    receipt_sha256: str

    def to_document(self) -> dict[str, str]:
        return {
            "implementation_sha256": self.implementation_sha256,
            "input_sha256": self.input_sha256,
            "proof_id": self.proof_id,
            "receipt_sha256": self.receipt_sha256,
            "status": self.status,
        }


def source_sha256(path: Path) -> str:
    """Hash an implementation or immutable input without reading it as text."""

    return sha256(path.read_bytes()).hexdigest()


def proof_receipt(
    proof_id: str, *, implementation_sha256: str, input_sha256: str, status: str = "passed"
) -> ProofReceipt:
    """Construct a self-verifying receipt with no outcome or secret material."""

    if proof_id not in REQUIRED_PROOF_IDS:
        raise ConformancePauseError("conformance_proof_unknown", proof_id)
    if status not in {"passed", "failed", "interrupted"}:
        raise ConformancePauseError("conformance_proof_status_invalid", proof_id)
    _require_sha256(implementation_sha256, "conformance_implementation_hash_invalid")
    _require_sha256(input_sha256, "conformance_input_hash_invalid")
    receipt_without_digest = {
        "implementation_sha256": implementation_sha256,
        "input_sha256": input_sha256,
        "proof_id": proof_id,
        "status": status,
    }
    return ProofReceipt(
        proof_id=proof_id,
        status=status,
        implementation_sha256=implementation_sha256,
        input_sha256=input_sha256,
        receipt_sha256=sha256(canonical_bytes(receipt_without_digest)).hexdigest(),
    )


def build_conformance_report(
    *,
    mode: str,
    stage_locks: Mapping[str, str],
    proof_receipts: tuple[ProofReceipt, ...],
    input_hashes: Mapping[str, str],
) -> dict[str, object]:
    """Create a sealed report identity from all proof and frozen-input hashes."""

    if mode not in {"unpaid_ci", "provider_qualification"}:
        raise ConformancePauseError("conformance_mode_invalid", mode)
    _validate_hash_mapping(stage_locks, REQUIRED_STAGE_LOCKS, "conformance_stage_locks_invalid")
    if set(input_hashes) == set() or any(
        not isinstance(name, str) or not name or not _is_sha256(value)
        for name, value in input_hashes.items()
    ):
        raise ConformancePauseError("conformance_inputs_incomplete")
    receipt_by_id = {receipt.proof_id: receipt for receipt in proof_receipts}
    if len(receipt_by_id) != len(proof_receipts) or set(receipt_by_id) != REQUIRED_PROOF_IDS:
        raise ConformancePauseError("conformance_proof_inventory_incomplete")
    receipts = [receipt_by_id[proof_id].to_document() for proof_id in sorted(REQUIRED_PROOF_IDS)]
    report = {
        "schema_version": CONFORMANCE_SCHEMA_VERSION,
        "artifact_type": "conformance_report",
        "evidence_label": CONFORMANCE_LABEL,
        "mode": mode,
        "stage_locks": dict(sorted(stage_locks.items())),
        "input_hashes": dict(sorted(input_hashes.items())),
        "proof_receipts": receipts,
        "status": (
            "passed" if all(receipt["status"] == "passed" for receipt in receipts) else "blocked"
        ),
    }
    report["report_id"] = sha256(canonical_bytes(report)).hexdigest()
    return attach_digest(report)


def report_bytes(report: Mapping[str, object]) -> bytes:
    """Return only a fully validated, canonical conformance authority."""

    payload = canonical_bytes(dict(report))
    load_conformance_report(payload)
    return payload


def load_conformance_report(data: bytes) -> dict[str, object]:
    """Load an exact report; malformed, stale, or incomplete evidence pauses work."""

    document = _canonical_document(data, "conformance_report_corrupt")
    required = {
        "schema_version",
        "artifact_type",
        "evidence_label",
        "mode",
        "stage_locks",
        "input_hashes",
        "proof_receipts",
        "status",
        "report_id",
        "digest",
    }
    if set(document) != required or document.get("schema_version") != CONFORMANCE_SCHEMA_VERSION:
        raise ConformancePauseError("conformance_report_schema_invalid")
    if document.get("artifact_type") != "conformance_report" or not verify_digest(document):
        raise ConformancePauseError("conformance_report_corrupt")
    if document.get("evidence_label") != CONFORMANCE_LABEL:
        raise ConformancePauseError("conformance_report_efficacy_label_forbidden")
    if document.get("mode") not in {"unpaid_ci", "provider_qualification"}:
        raise ConformancePauseError("conformance_mode_invalid")
    stage_locks = _mapping(document["stage_locks"], "conformance_stage_locks_invalid")
    _validate_hash_mapping(stage_locks, REQUIRED_STAGE_LOCKS, "conformance_stage_locks_invalid")
    inputs = _mapping(document["input_hashes"], "conformance_inputs_incomplete")
    if not inputs or any(
        not isinstance(key, str) or not key or not _is_sha256(value)
        for key, value in inputs.items()
    ):
        raise ConformancePauseError("conformance_inputs_incomplete")
    proof_receipts = document["proof_receipts"]
    if not isinstance(proof_receipts, list):
        raise ConformancePauseError("conformance_proof_inventory_incomplete")
    receipts = tuple(_load_receipt(item) for item in proof_receipts)
    if {receipt.proof_id for receipt in receipts} != REQUIRED_PROOF_IDS or len(receipts) != len(
        REQUIRED_PROOF_IDS
    ):
        raise ConformancePauseError("conformance_proof_inventory_incomplete")
    report_without_id = dict(document)
    recorded_digest = report_without_id.pop("digest")
    recorded_id = report_without_id.pop("report_id")
    if not isinstance(recorded_digest, str) or not _is_sha256(recorded_id):
        raise ConformancePauseError("conformance_report_corrupt")
    if sha256(canonical_bytes(report_without_id)).hexdigest() != recorded_id:
        raise ConformancePauseError("conformance_report_identity_conflict")
    expected = attach_digest({**report_without_id, "report_id": recorded_id})
    if expected != document or canonical_bytes(document) != data:
        raise ConformancePauseError("conformance_report_corrupt")
    if document.get("status") != "passed" or any(
        receipt.status != "passed" for receipt in receipts
    ):
        raise ConformancePauseError("conformance_proof_failed")
    return document


def require_enrollment_conformance(
    data: bytes, stage_locks: Mapping[str, str] | None = None
) -> dict[str, object]:
    """Authorize only a current complete report, optionally bound to exact entry locks."""

    report = load_conformance_report(data)
    if stage_locks is not None:
        _validate_hash_mapping(stage_locks, REQUIRED_STAGE_LOCKS, "conformance_stage_locks_invalid")
        if report["stage_locks"] != dict(sorted(stage_locks.items())):
            raise StageControlError("conformance_report_stale")
    return report


def write_conformance_report(root: Path, report: Mapping[str, object]) -> Path:
    """Persist a report by identity without overwriting another result."""

    payload = report_bytes(report)
    report_id = str(load_conformance_report(payload)["report_id"])
    destination = root / "conformance-reports" / f"{report_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if not destination.is_file() or destination.read_bytes() != payload:
            raise ConformancePauseError("conformance_report_identity_conflict") from None
    return destination


def write_bootstrap_receipt(
    root: Path, *, runtime_lock: Mapping[str, object], telemetry_sha256: str, backup_sha256: str
) -> Path:
    """Seal the bootstrap prerequisites without granting enrollment authority."""

    lock_sha256 = runtime_lock.get("lock_sha256")
    _require_sha256(lock_sha256, "bootstrap_runtime_lock_invalid")
    _require_sha256(telemetry_sha256, "bootstrap_telemetry_hash_invalid")
    _require_sha256(backup_sha256, "bootstrap_backup_hash_invalid")
    document = attach_digest(
        {
            "schema_version": CONFORMANCE_SCHEMA_VERSION,
            "artifact_type": "bootstrap_receipt",
            "evidence_label": CONFORMANCE_LABEL,
            "python_version": sys.version.split()[0],
            "runtime_lock_sha256": lock_sha256,
            "telemetry_sha256": telemetry_sha256,
            "backup_sha256": backup_sha256,
            "runtime_download_disabled": os.environ.get("COPILOT_SKIP_CLI_DOWNLOAD") == "1",
        }
    )
    payload = canonical_bytes(document)
    destination = root / "bootstrap-receipts" / f"{document['digest']}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if destination.read_bytes() != payload:
            raise ConformancePauseError("bootstrap_receipt_conflict") from None
    return destination


def synthetic_proof_receipts(
    *, input_sha256: str, implementation_root: Path
) -> tuple[ProofReceipt, ...]:
    """Build deterministic unpaid receipts from concrete evaluator implementation bytes.

    The vertical slice supplies the shared catalog-to-report input hash.  Each
    receipt additionally binds the source module that owns its contract so a source
    change invalidates the report and requires a new conformance identity.
    """

    _require_sha256(input_sha256, "conformance_input_hash_invalid")
    implementations = (
        "src/memrelay_eval/catalog/compiler.py",
        "src/memrelay_eval/catalog/canonical.py",
        "src/memrelay_eval/catalog/validation.py",
        "src/memrelay_eval/orchestration/planning.py",
        "src/memrelay_eval/adapters/copilot/catalog.py",
        "src/memrelay_eval/application/copilot_catalog.py",
        "src/memrelay_eval/adapters/copilot/client.py",
        "src/memrelay_eval/adapters/process/environment.py",
        "src/memrelay_eval/adapters/memrelay/controls.py",
        "src/memrelay_eval/orchestration/parity.py",
        "src/memrelay_eval/adapters/telemetry/semantics.py",
        "src/memrelay_eval/evidence/costs.py",
        "src/memrelay_eval/adapters/inspect/export.py",
        "src/memrelay_eval/adapters/telemetry/otel.py",
        "collector/semantic-map.yaml",
        "src/memrelay_eval/evidence/parquet.py",
        "src/memrelay_eval/adapters/workspace/worktree.py",
        "src/memrelay_eval/adapters/workspace/clone.py",
        "src/memrelay_eval/adapters/grader/executable.py",
        "src/memrelay_eval/scoring/blinding.py",
        "src/memrelay_eval/adapters/artifacts/filesystem.py",
        "src/memrelay_eval/evidence/backup.py",
        "src/memrelay_eval/evidence/reconcile.py",
        "src/memrelay_eval/analysis/replay.py",
        "src/memrelay_eval/analysis/queries.py",
        "src/memrelay_eval/analysis/reports.py",
        "src/memrelay_eval/orchestration/stages.py",
    )
    if len(implementations) != len(REQUIRED_PROOF_IDS):
        raise AssertionError("conformance proof implementation inventory drift")
    return tuple(
        proof_receipt(
            proof_id,
            implementation_sha256=source_sha256(implementation_root / implementation),
            input_sha256=input_sha256,
        )
        for proof_id, implementation in zip(
            sorted(REQUIRED_PROOF_IDS), implementations, strict=True
        )
    )


def _load_receipt(value: object) -> ProofReceipt:
    document = _mapping(value, "conformance_proof_receipt_invalid")
    if set(document) != {
        "proof_id",
        "status",
        "implementation_sha256",
        "input_sha256",
        "receipt_sha256",
    }:
        raise ConformancePauseError("conformance_proof_receipt_invalid")
    try:
        receipt = ProofReceipt(
            proof_id=_string(document["proof_id"], "conformance_proof_receipt_invalid"),
            status=_string(document["status"], "conformance_proof_receipt_invalid"),
            implementation_sha256=_string(
                document["implementation_sha256"], "conformance_proof_receipt_invalid"
            ),
            input_sha256=_string(document["input_sha256"], "conformance_proof_receipt_invalid"),
            receipt_sha256=_string(document["receipt_sha256"], "conformance_proof_receipt_invalid"),
        )
    except ValueError as error:
        raise ConformancePauseError("conformance_proof_receipt_invalid") from error
    expected = proof_receipt(
        receipt.proof_id,
        implementation_sha256=receipt.implementation_sha256,
        input_sha256=receipt.input_sha256,
        status=receipt.status,
    )
    if expected != receipt:
        raise ConformancePauseError("conformance_proof_receipt_invalid")
    return receipt


def _canonical_document(data: bytes, code: str) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ConformancePauseError(code)
            result[key] = value
        return result

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicates)
        canonical = canonical_bytes(document)
    except (UnicodeDecodeError, json.JSONDecodeError, CanonicalizationError) as error:
        raise ConformancePauseError(code) from error
    if not isinstance(document, dict) or canonical != data:
        raise ConformancePauseError(code)
    return document


def _validate_hash_mapping(
    value: Mapping[str, object], required: frozenset[str], code: str
) -> None:
    if set(value) != required or any(not _is_sha256(item) for item in value.values()):
        raise ConformancePauseError(code)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConformancePauseError(code)
    return value


def _string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConformancePauseError(code)
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_sha256(value: object, code: str) -> str:
    if not _is_sha256(value):
        raise ConformancePauseError(code)
    return value
