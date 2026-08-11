"""Authority-owned executable bootstrap and conformance evidence."""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from memrelay_eval.canonical import (
    CanonicalizationError,
    attach_digest,
    canonical_bytes,
    verify_digest,
)
from memrelay_eval.domain.errors import ConformancePauseError, StageControlError

CONFORMANCE_SCHEMA_VERSION = "2.0.0"
CONFORMANCE_LABEL = "conformance_not_efficacy_evidence"
ProofStatus = Literal["passed", "failed", "unavailable", "interrupted"]
ConformanceMode = Literal["unpaid_ci", "provider_qualification"]

# Each identifier has one authority-owned executable probe.  A report cannot
# substitute a source hash, checkbox, or aggregate percentage for this inventory.
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
        "REPRICING",
        "REPLAY-SEALED",
        "REPORT-EVIDENCE-LINK",
        "CROSS-REPOSITORY-DENY",
        "NO-NETWORK",
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
class ProbeResult:
    """Observed terminal output from exactly one executable proof."""

    status: ProofStatus
    terminal: str
    input_hashes: Mapping[str, str]
    output_hashes: Mapping[str, str]
    failure_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed", "unavailable", "interrupted"}:
            raise ConformancePauseError("conformance_probe_status_invalid", "invalid proof status")
        if not self.terminal:
            raise ConformancePauseError(
                "conformance_probe_terminal_missing", "proof terminal missing"
            )
        _validate_named_hashes(self.input_hashes, "conformance_probe_inputs_invalid")
        _validate_named_hashes(self.output_hashes, "conformance_probe_outputs_invalid")
        if not self.output_hashes:
            raise ConformancePauseError(
                "conformance_probe_outputs_missing", "a proof must retain observed output"
            )
        if self.status == "passed" and self.failure_evidence_sha256 is not None:
            raise ConformancePauseError(
                "conformance_probe_failure_evidence_conflict",
                "a passed proof cannot carry failure evidence",
            )
        if self.status != "passed" and not _is_sha256(self.failure_evidence_sha256):
            raise ConformancePauseError(
                "conformance_probe_failure_evidence_missing",
                "a non-passing proof must retain failure evidence",
            )


@dataclass(frozen=True, slots=True)
class ProofReceipt:
    """Canonical receipt tied to an observed proof execution, not source text."""

    proof_id: str
    status: ProofStatus
    terminal: str
    implementation_sha256: str
    runtime_lock_sha256: str
    protocol_sha256: str
    environment_sha256: str
    input_hashes: Mapping[str, str]
    output_hashes: Mapping[str, str]
    failure_evidence_sha256: str | None
    receipt_sha256: str

    def to_document(self) -> dict[str, object]:
        return {
            "environment_sha256": self.environment_sha256,
            "failure_evidence_sha256": self.failure_evidence_sha256,
            "implementation_sha256": self.implementation_sha256,
            "input_hashes": dict(sorted(self.input_hashes.items())),
            "output_hashes": dict(sorted(self.output_hashes.items())),
            "proof_id": self.proof_id,
            "protocol_sha256": self.protocol_sha256,
            "receipt_sha256": self.receipt_sha256,
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "status": self.status,
            "terminal": self.terminal,
        }


@dataclass(frozen=True, slots=True)
class ConformanceContext:
    """Frozen authority available to every probe in one conformance invocation."""

    mode: ConformanceMode
    evaluation_root: Path
    run_root: Path
    stage_locks: Mapping[str, str]
    bootstrap_receipt: Mapping[str, object]

    @property
    def runtime_lock_sha256(self) -> str:
        return self.stage_locks["runtime_lock_sha256"]

    @property
    def protocol_sha256(self) -> str:
        return self.stage_locks["protocol_sha256"]

    @property
    def environment_sha256(self) -> str:
        return self.stage_locks["environment_sha256"]


ProbeExecutor = Callable[[ConformanceContext], ProbeResult]


@dataclass(frozen=True, slots=True)
class ConformanceProbe:
    """One non-optional executable proof with stable implementation identity."""

    proof_id: str
    implementation: str
    execute: ProbeExecutor

    def __post_init__(self) -> None:
        if self.proof_id not in REQUIRED_PROOF_IDS or not self.implementation:
            raise ConformancePauseError(
                "conformance_probe_definition_invalid", "proof definition is invalid"
            )

    @property
    def implementation_sha256(self) -> str:
        return sha256(
            canonical_bytes(
                {
                    "artifact_type": "conformance_probe_implementation",
                    "implementation": self.implementation,
                    "proof_id": self.proof_id,
                    "schema_version": CONFORMANCE_SCHEMA_VERSION,
                }
            )
        ).hexdigest()


class ProofRegistry:
    """Executes the complete proof closure exactly once, failing closed on gaps."""

    def __init__(self, probes: tuple[ConformanceProbe, ...]) -> None:
        by_id = {probe.proof_id: probe for probe in probes}
        if len(by_id) != len(probes) or set(by_id) != REQUIRED_PROOF_IDS:
            raise ConformancePauseError(
                "conformance_probe_inventory_incomplete",
                "every mandatory proof requires exactly one executable probe",
            )
        self._probes = by_id

    def execute(self, context: ConformanceContext) -> tuple[ProofReceipt, ...]:
        receipts: list[ProofReceipt] = []
        for proof_id in sorted(REQUIRED_PROOF_IDS):
            probe = self._probes[proof_id]
            try:
                result = probe.execute(context)
            except KeyboardInterrupt:
                raise
            except Exception as error:
                result = failed_probe_result(
                    "probe_exception",
                    {
                        "exception_type": type(error).__name__,
                        "proof_id": proof_id,
                    },
                )
            if not isinstance(result, ProbeResult):
                raise ConformancePauseError(
                    "conformance_probe_no_observation",
                    f"{proof_id} returned no observed terminal result",
                )
            receipts.append(receipt_from_result(probe, context, result))
        return tuple(receipts)


def receipt_from_result(
    probe: ConformanceProbe, context: ConformanceContext, result: ProbeResult
) -> ProofReceipt:
    """Bind one observed terminal result to its frozen runtime and environment."""

    document = {
        "environment_sha256": context.environment_sha256,
        "failure_evidence_sha256": result.failure_evidence_sha256,
        "implementation_sha256": probe.implementation_sha256,
        "input_hashes": dict(sorted(result.input_hashes.items())),
        "output_hashes": dict(sorted(result.output_hashes.items())),
        "proof_id": probe.proof_id,
        "protocol_sha256": context.protocol_sha256,
        "runtime_lock_sha256": context.runtime_lock_sha256,
        "status": result.status,
        "terminal": result.terminal,
    }
    return ProofReceipt(
        proof_id=probe.proof_id,
        status=result.status,
        terminal=result.terminal,
        implementation_sha256=probe.implementation_sha256,
        runtime_lock_sha256=context.runtime_lock_sha256,
        protocol_sha256=context.protocol_sha256,
        environment_sha256=context.environment_sha256,
        input_hashes=dict(result.input_hashes),
        output_hashes=dict(result.output_hashes),
        failure_evidence_sha256=result.failure_evidence_sha256,
        receipt_sha256=sha256(canonical_bytes(document)).hexdigest(),
    )


def observed_probe_result(
    *,
    input_documents: Mapping[str, object],
    output_documents: Mapping[str, object],
    terminal: str = "passed",
) -> ProbeResult:
    """Create a passed result only from concrete observed input and output documents."""

    return ProbeResult(
        status="passed",
        terminal=terminal,
        input_hashes=_hash_documents(input_documents),
        output_hashes=_hash_documents(output_documents),
    )


def failed_probe_result(terminal: str, evidence: Mapping[str, object]) -> ProbeResult:
    """Retain a typed, value-free terminal failure observation."""

    payload = canonical_bytes(dict(evidence))
    digest = sha256(payload).hexdigest()
    return ProbeResult(
        status="failed",
        terminal=terminal,
        input_hashes={"failure_contract": digest},
        output_hashes={"failure_observation": digest},
        failure_evidence_sha256=digest,
    )


def pytest_probe(test_target: str) -> ProbeExecutor:
    """Execute one real evaluator contract through an isolated pytest subprocess."""

    def execute(context: ConformanceContext) -> ProbeResult:
        command = (sys.executable, "-m", "pytest", "-q", test_target)
        environment = dict(os.environ)
        environment.pop("COPILOT_TOKEN", None)
        environment.pop("GH_TOKEN", None)
        environment.pop("GITHUB_TOKEN", None)
        environment.pop("OPENAI_API_KEY", None)
        environment["COPILOT_SKIP_CLI_DOWNLOAD"] = "1"
        completed = subprocess.run(
            command,
            cwd=context.evaluation_root,
            env=environment,
            capture_output=True,
            check=False,
        )
        output = completed.stdout + b"\n--- stderr ---\n" + completed.stderr
        inputs = {
            "command": canonical_bytes({"command": command, "target": test_target}),
            "mode": canonical_bytes({"mode": context.mode}),
        }
        outputs = {
            "pytest_output": output,
            "returncode": canonical_bytes({"returncode": completed.returncode}),
        }
        if completed.returncode == 0:
            return ProbeResult(
                status="passed",
                terminal="pytest_contract_passed",
                input_hashes={name: sha256(value).hexdigest() for name, value in inputs.items()},
                output_hashes={name: sha256(value).hexdigest() for name, value in outputs.items()},
            )
        evidence = {
            "command_sha256": sha256(inputs["command"]).hexdigest(),
            "output_sha256": sha256(output).hexdigest(),
            "returncode": completed.returncode,
        }
        return ProbeResult(
            status="failed",
            terminal="pytest_contract_failed",
            input_hashes=_hash_documents(inputs),
            output_hashes=_hash_documents(outputs),
            failure_evidence_sha256=sha256(canonical_bytes(evidence)).hexdigest(),
        )

    return execute


_UNPAID_PROBE_TARGETS: Mapping[str, str] = {
    "CATALOG-SCHEMA": "tests/unit/catalog/test_validation.py",
    "CATALOG-HASHES": "tests/contract/catalog/test_compiler_determinism.py",
    "CATALOG-MAPPINGS": "tests/contract/catalog/test_traceability.py",
    "CATALOG-CI": "tests/integration/test_offline_catalog_to_manifest.py",
    "MODEL-CATALOG-SNAPSHOT": "tests/unit/copilot/test_catalog.py",
    "MODEL-SELECTION-PIN": "tests/contract/copilot/test_model_lock_idempotency.py",
    "AUTH-COPILOT-SUBSCRIPTION": "tests/contract/copilot/test_sdk_contract.py",
    "AUTH-SDK-BYOK-DENY": "tests/contract/copilot/test_sdk_contract.py",
    "SECRET-OPENAI-ISOLATION": "tests/security/test_provider_credential_boundaries.py",
    "FRAMEWORK-ROUTE-FAIL-CLOSED": "tests/contract/memrelay/test_provider_strategy.py",
    "ARM-PARITY": "tests/unit/orchestration/test_parity.py",
    "TEL-PROVIDER-IDENTITY": "tests/contract/telemetry/test_semantics.py",
    "COST-PROVIDER-LEDGERS": "tests/integration/costs/test_provider_ledgers.py",
    "TOOL-INSPECT-CONFORMANCE": "tests/contract/inspect/test_inspect_copilot_contract.py",
    "TOOL-OTEL-CONFORMANCE": "tests/integration/telemetry/test_bootstrap.py",
    "TOOL-OPENINFERENCE-CONFORMANCE": "tests/contract/telemetry/test_semantics.py",
    "TOOL-PARQUET-CONFORMANCE": "tests/contract/analysis/test_parquet_schemas.py",
    "WORKSPACE-TEMPORARY-WORKTREE": "tests/contract/workspace/test_isolation.py",
    "WORKSPACE-ISOLATED-CLONE": "tests/contract/workspace/test_isolation.py",
    "GRADER-CONTRACT": "tests/unit/scoring/test_deterministic_grader.py",
    "JUDGE-BLINDING": "tests/unit/scoring/test_blinding.py",
    "CAS-CORRUPTION": "tests/fault/artifacts/test_cas_crash_corruption.py",
    "BACKUP-RESTORE-SECOND-VOLUME": "tests/integration/evidence/test_restore_drill.py",
    "RECONCILIATION-COMPLETENESS": "tests/fault/evidence/test_reconcile_fail_closed.py",
    "REPRICING": "tests/unit/costs/test_repricing.py",
    "REPLAY-SEALED": "tests/integration/reproduction/test_sealed_replay.py",
    "REPORT-EVIDENCE-LINK": "tests/unit/analysis/test_reports.py",
    "CROSS-REPOSITORY-DENY": "tests/contract/test_pre_discovery_authorization.py",
    "NO-NETWORK": "tests/architecture/test_offline_planning_architecture.py",
}


def unpaid_proof_registry() -> ProofRegistry:
    """Return executable fake-provider contract probes for credential-free CI."""

    return ProofRegistry(
        tuple(
            ConformanceProbe(
                proof_id=proof_id,
                implementation=f"pytest/{target}",
                execute=pytest_probe(target),
            )
            for proof_id, target in sorted(_UNPAID_PROBE_TARGETS.items())
        )
    )


def provider_proof_registry(
    provider_probe: Callable[[str, ConformanceContext], ProbeResult],
) -> ProofRegistry:
    """Run real provider-bound probes only through an explicit provider executor.

    The local contract probes remain executable in qualification mode, while the
    three calls that require a Copilot subscription cross the supplied official
    adapter boundary. Test doubles may supply this boundary, but production callers
    must supply a real executor and any unavailable capability is non-passing.
    """

    provider_ids = {
        "AUTH-COPILOT-SUBSCRIPTION",
        "MODEL-CATALOG-SNAPSHOT",
        "MODEL-SELECTION-PIN",
    }
    return ProofRegistry(
        tuple(
            ConformanceProbe(
                proof_id=proof_id,
                implementation=(
                    "official-provider-qualification/v1"
                    if proof_id in provider_ids
                    else f"pytest/{target}"
                ),
                execute=(
                    lambda context, proof_id=proof_id, target=target: (
                        provider_probe(proof_id, context)
                        if proof_id in provider_ids
                        else pytest_probe(target)(context)
                    )
                ),
            )
            for proof_id, target in sorted(_UNPAID_PROBE_TARGETS.items())
        )
    )


def build_conformance_report(
    *,
    mode: ConformanceMode,
    stage_locks: Mapping[str, str],
    proof_receipts: tuple[ProofReceipt, ...],
    input_hashes: Mapping[str, str],
    bootstrap_receipt_sha256: str,
) -> dict[str, object]:
    """Create a sealed report from observed receipts and the exact bootstrap authority."""

    _validate_mode(mode)
    _validate_stage_locks(stage_locks)
    _validate_named_hashes(input_hashes, "conformance_inputs_incomplete")
    _require_sha256(bootstrap_receipt_sha256, "bootstrap_receipt_hash_invalid")
    receipts = _validate_receipts(proof_receipts, stage_locks)
    report = {
        "schema_version": CONFORMANCE_SCHEMA_VERSION,
        "artifact_type": "conformance_report",
        "evidence_label": CONFORMANCE_LABEL,
        "mode": mode,
        "bootstrap_receipt_sha256": bootstrap_receipt_sha256,
        "stage_locks": dict(sorted(stage_locks.items())),
        "input_hashes": dict(sorted(input_hashes.items())),
        "proof_receipts": [receipt.to_document() for receipt in receipts],
        "status": "passed"
        if all(receipt.status == "passed" for receipt in receipts)
        else "blocked",
    }
    report["report_id"] = sha256(canonical_bytes(report)).hexdigest()
    return attach_digest(report)


def report_bytes(report: Mapping[str, object]) -> bytes:
    """Return only a fully validated, canonical report authority."""

    payload = canonical_bytes(dict(report))
    load_conformance_report(payload, require_passed=False)
    return payload


def load_conformance_report(data: bytes, *, require_passed: bool = True) -> dict[str, object]:
    """Load a complete observed report; never infer passing status from metadata."""

    document = _canonical_document(data, "conformance_report_corrupt")
    required = {
        "schema_version",
        "artifact_type",
        "evidence_label",
        "mode",
        "bootstrap_receipt_sha256",
        "stage_locks",
        "input_hashes",
        "proof_receipts",
        "status",
        "report_id",
        "digest",
    }
    if set(document) != required or document.get("schema_version") != CONFORMANCE_SCHEMA_VERSION:
        raise ConformancePauseError("conformance_report_schema_invalid", "report schema is invalid")
    if document.get("artifact_type") != "conformance_report" or not verify_digest(document):
        raise ConformancePauseError("conformance_report_corrupt", "report digest is invalid")
    if document.get("evidence_label") != CONFORMANCE_LABEL:
        raise ConformancePauseError(
            "conformance_report_efficacy_label_forbidden", "conformance cannot be efficacy evidence"
        )
    _validate_mode(document.get("mode"))
    _require_sha256(document.get("bootstrap_receipt_sha256"), "bootstrap_receipt_hash_invalid")
    stage_locks = _mapping(document["stage_locks"], "conformance_stage_locks_invalid")
    _validate_stage_locks(stage_locks)
    input_hashes = _mapping(document["input_hashes"], "conformance_inputs_incomplete")
    _validate_named_hashes(input_hashes, "conformance_inputs_incomplete")
    raw_receipts = document["proof_receipts"]
    if not isinstance(raw_receipts, list):
        raise ConformancePauseError(
            "conformance_proof_inventory_incomplete", "report receipts must be a list"
        )
    receipts = tuple(_load_receipt(item) for item in raw_receipts)
    _validate_receipts(receipts, stage_locks)
    without_identity = dict(document)
    digest = without_identity.pop("digest")
    report_id = without_identity.pop("report_id")
    if not _is_sha256(digest) or not _is_sha256(report_id):
        raise ConformancePauseError("conformance_report_corrupt", "report identity is invalid")
    if sha256(canonical_bytes(without_identity)).hexdigest() != report_id:
        raise ConformancePauseError(
            "conformance_report_identity_conflict", "report identity does not match contents"
        )
    if attach_digest({**without_identity, "report_id": report_id}) != document:
        raise ConformancePauseError("conformance_report_corrupt", "report bytes are not canonical")
    if require_passed and (
        document.get("status") != "passed"
        or any(receipt.status != "passed" for receipt in receipts)
    ):
        raise ConformancePauseError(
            "conformance_proof_failed", "one or more observed proofs failed"
        )
    return document


def require_enrollment_conformance(
    data: bytes,
    *,
    bootstrap_data: bytes,
    stage_locks: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Require intact, environment-bound bootstrap and report authorities before enrollment."""

    report = load_conformance_report(data)
    bootstrap = load_bootstrap_receipt(bootstrap_data)
    if sha256(bootstrap_data).hexdigest() != report["bootstrap_receipt_sha256"]:
        raise StageControlError("bootstrap_receipt_report_link_mismatch")
    if stage_locks is not None:
        _validate_stage_locks(stage_locks)
        if report["stage_locks"] != dict(sorted(stage_locks.items())):
            raise StageControlError("conformance_report_stale")
        if bootstrap["runtime_lock_sha256"] != stage_locks["runtime_lock_sha256"]:
            raise StageControlError("bootstrap_runtime_lock_stale")
        if bootstrap["environment_sha256"] != stage_locks["environment_sha256"]:
            raise StageControlError("bootstrap_environment_stale")
    return report


def build_bootstrap_receipt(
    *,
    mode: ConformanceMode,
    runtime_lock: Mapping[str, object],
    input_hashes: Mapping[str, str],
    output_hashes: Mapping[str, str],
    environment_sha256: str,
    protocol_sha256: str,
    status: ProofStatus = "passed",
    failure_evidence_sha256: str | None = None,
    runtime_download_disabled: bool = True,
) -> dict[str, object]:
    """Bind observed bootstrap output to the runtime, environment, and protocol."""

    _validate_mode(mode)
    runtime_lock_sha256 = _require_sha256(
        runtime_lock.get("lock_sha256"), "bootstrap_runtime_lock_invalid"
    )
    _validate_named_hashes(input_hashes, "bootstrap_inputs_invalid")
    _validate_named_hashes(output_hashes, "bootstrap_outputs_invalid")
    if not output_hashes:
        raise ConformancePauseError("bootstrap_outputs_missing", "bootstrap has no observed output")
    _require_sha256(environment_sha256, "bootstrap_environment_invalid")
    _require_sha256(protocol_sha256, "bootstrap_protocol_invalid")
    result = ProbeResult(
        status=status,
        terminal="bootstrap_passed" if status == "passed" else "bootstrap_failed",
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        failure_evidence_sha256=failure_evidence_sha256,
    )
    document = {
        "schema_version": CONFORMANCE_SCHEMA_VERSION,
        "artifact_type": "bootstrap_receipt",
        "evidence_label": CONFORMANCE_LABEL,
        "mode": mode,
        "status": result.status,
        "terminal": result.terminal,
        "runtime_lock_sha256": runtime_lock_sha256,
        "environment_sha256": environment_sha256,
        "protocol_sha256": protocol_sha256,
        "input_hashes": dict(sorted(result.input_hashes.items())),
        "output_hashes": dict(sorted(result.output_hashes.items())),
        "failure_evidence_sha256": result.failure_evidence_sha256,
        "runtime_download_disabled": runtime_download_disabled,
    }
    return attach_digest(document)


def bootstrap_receipt_bytes(receipt: Mapping[str, object]) -> bytes:
    payload = canonical_bytes(dict(receipt))
    load_bootstrap_receipt(payload)
    return payload


def load_bootstrap_receipt(data: bytes) -> dict[str, object]:
    """Verify a real bootstrap receipt before it can feed conformance."""

    document = _canonical_document(data, "bootstrap_receipt_corrupt")
    required = {
        "schema_version",
        "artifact_type",
        "evidence_label",
        "mode",
        "status",
        "terminal",
        "runtime_lock_sha256",
        "environment_sha256",
        "protocol_sha256",
        "input_hashes",
        "output_hashes",
        "failure_evidence_sha256",
        "runtime_download_disabled",
        "digest",
    }
    if set(document) != required or document.get("schema_version") != CONFORMANCE_SCHEMA_VERSION:
        raise ConformancePauseError(
            "bootstrap_receipt_schema_invalid", "bootstrap schema is invalid"
        )
    if document.get("artifact_type") != "bootstrap_receipt" or not verify_digest(document):
        raise ConformancePauseError("bootstrap_receipt_corrupt", "bootstrap digest is invalid")
    if document.get("evidence_label") != CONFORMANCE_LABEL:
        raise ConformancePauseError(
            "bootstrap_receipt_efficacy_label_forbidden", "bootstrap cannot be efficacy evidence"
        )
    _validate_mode(document.get("mode"))
    for key in ("runtime_lock_sha256", "environment_sha256", "protocol_sha256"):
        _require_sha256(document.get(key), "bootstrap_receipt_hash_invalid")
    result = ProbeResult(
        status=_status(document.get("status")),
        terminal=_nonempty_string(document.get("terminal"), "bootstrap_receipt_terminal_invalid"),
        input_hashes=_mapping(document["input_hashes"], "bootstrap_inputs_invalid"),
        output_hashes=_mapping(document["output_hashes"], "bootstrap_outputs_invalid"),
        failure_evidence_sha256=document.get("failure_evidence_sha256"),
    )
    if not isinstance(document.get("runtime_download_disabled"), bool):
        raise ConformancePauseError(
            "bootstrap_runtime_download_guard_invalid", "bootstrap download guard is invalid"
        )
    if result.status != "passed" or not document["runtime_download_disabled"]:
        raise ConformancePauseError("bootstrap_receipt_failed", "bootstrap did not complete")
    return document


def write_bootstrap_receipt(root: Path, receipt: Mapping[str, object]) -> Path:
    """Write bootstrap evidence once using a hardened immutable publication path."""

    payload = bootstrap_receipt_bytes(receipt)
    digest = str(load_bootstrap_receipt(payload)["digest"])
    return _publish_immutable(root / "bootstrap-receipts" / f"{digest}.json", payload, "bootstrap")


def write_conformance_report(root: Path, report: Mapping[str, object]) -> Path:
    """Write report evidence once using the same hardened publication path."""

    payload = report_bytes(report)
    report_id = str(load_conformance_report(payload, require_passed=False)["report_id"])
    return _publish_immutable(
        root / "conformance-reports" / f"{report_id}.json", payload, "conformance_report"
    )


def _publish_immutable(destination: Path, payload: bytes, kind: str) -> Path:
    """Publish bytes with exclusive linking and path identity checks.

    The destination and every parent must be ordinary directories. The staged file
    is opened without following links, fsynced, then hard-linked into place so a
    concurrent publisher either wins with exact bytes or observes a conflict.
    """

    _assert_safe_parent(destination.parent, kind)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_parent(destination.parent, kind)
    if _path_exists(destination):
        _verify_existing(destination, payload, kind)
        return destination
    staging = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.staging")
    descriptor: int | None = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(staging, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        staged = staging.lstat()
        if not stat.S_ISREG(staged.st_mode) or staged.st_nlink != 1:
            raise ConformancePauseError(
                f"{kind}_staging_path_invalid", "immutable staging path is not a private file"
            )
        published = False
        try:
            os.link(staging, destination)
        except FileExistsError:
            pass
        else:
            published = True
    except OSError as error:
        raise ConformancePauseError(
            f"{kind}_publish_failed", "immutable publication failed"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
    _verify_existing(destination, payload, kind)
    if published:
        _fsync_directory(destination.parent)
    return destination


def _assert_safe_parent(parent: Path, kind: str) -> None:
    current = parent
    missing: list[Path] = []
    while not _path_exists(current):
        missing.append(current)
        if current.parent == current:
            raise ConformancePauseError(
                f"{kind}_parent_invalid", "publication parent is unavailable"
            )
        current = current.parent
    status = current.lstat()
    if not stat.S_ISDIR(status.st_mode) or current.is_symlink():
        raise ConformancePauseError(
            f"{kind}_parent_unsafe", "publication parent is not a directory"
        )
    for directory in reversed(missing):
        directory.mkdir()
        status = directory.lstat()
        if not stat.S_ISDIR(status.st_mode) or directory.is_symlink():
            raise ConformancePauseError(f"{kind}_parent_unsafe", "publication parent is not safe")


def _verify_existing(path: Path, payload: bytes, kind: str) -> None:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or path.is_symlink():
            raise ConformancePauseError(
                f"{kind}_existing_path_invalid", "existing authority is unsafe"
            )
        with path.open("rb") as stream:
            actual = stream.read()
            after = os.fstat(stream.fileno())
    except OSError as error:
        raise ConformancePauseError(
            f"{kind}_existing_path_invalid", "existing authority is unreadable"
        ) from error
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
    ):
        raise ConformancePauseError(
            f"{kind}_existing_path_toctou", "authority path changed while read"
        )
    if actual != payload:
        raise ConformancePauseError(f"{kind}_identity_conflict", "authority identity conflicts")


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _validate_receipts(
    receipts: tuple[ProofReceipt, ...], stage_locks: Mapping[str, object]
) -> tuple[ProofReceipt, ...]:
    by_id = {receipt.proof_id: receipt for receipt in receipts}
    if len(by_id) != len(receipts) or set(by_id) != REQUIRED_PROOF_IDS:
        raise ConformancePauseError(
            "conformance_proof_inventory_incomplete",
            "report does not contain exactly one receipt for every proof",
        )
    ordered = tuple(by_id[proof_id] for proof_id in sorted(REQUIRED_PROOF_IDS))
    for receipt in ordered:
        if (
            receipt.runtime_lock_sha256 != stage_locks["runtime_lock_sha256"]
            or receipt.protocol_sha256 != stage_locks["protocol_sha256"]
            or receipt.environment_sha256 != stage_locks["environment_sha256"]
        ):
            raise ConformancePauseError(
                "conformance_proof_authority_conflict", "receipt authority does not match report"
            )
    return ordered


def _load_receipt(value: object) -> ProofReceipt:
    document = _mapping(value, "conformance_proof_receipt_invalid")
    required = {
        "proof_id",
        "status",
        "terminal",
        "implementation_sha256",
        "runtime_lock_sha256",
        "protocol_sha256",
        "environment_sha256",
        "input_hashes",
        "output_hashes",
        "failure_evidence_sha256",
        "receipt_sha256",
    }
    if set(document) != required:
        raise ConformancePauseError(
            "conformance_proof_receipt_invalid", "proof receipt schema is invalid"
        )
    proof_id = _nonempty_string(document["proof_id"], "conformance_proof_receipt_invalid")
    if proof_id not in REQUIRED_PROOF_IDS:
        raise ConformancePauseError("conformance_proof_unknown", "proof is not required")
    receipt = ProofReceipt(
        proof_id=proof_id,
        status=_status(document["status"]),
        terminal=_nonempty_string(document["terminal"], "conformance_proof_receipt_invalid"),
        implementation_sha256=_require_sha256(
            document["implementation_sha256"], "conformance_proof_receipt_invalid"
        ),
        runtime_lock_sha256=_require_sha256(
            document["runtime_lock_sha256"], "conformance_proof_receipt_invalid"
        ),
        protocol_sha256=_require_sha256(
            document["protocol_sha256"], "conformance_proof_receipt_invalid"
        ),
        environment_sha256=_require_sha256(
            document["environment_sha256"], "conformance_proof_receipt_invalid"
        ),
        input_hashes=_mapping(document["input_hashes"], "conformance_proof_receipt_invalid"),
        output_hashes=_mapping(document["output_hashes"], "conformance_proof_receipt_invalid"),
        failure_evidence_sha256=document["failure_evidence_sha256"],
        receipt_sha256=_require_sha256(
            document["receipt_sha256"], "conformance_proof_receipt_invalid"
        ),
    )
    observed = ProbeResult(
        status=receipt.status,
        terminal=receipt.terminal,
        input_hashes=receipt.input_hashes,
        output_hashes=receipt.output_hashes,
        failure_evidence_sha256=receipt.failure_evidence_sha256,
    )
    expected = {
        "environment_sha256": receipt.environment_sha256,
        "failure_evidence_sha256": observed.failure_evidence_sha256,
        "implementation_sha256": receipt.implementation_sha256,
        "input_hashes": dict(sorted(observed.input_hashes.items())),
        "output_hashes": dict(sorted(observed.output_hashes.items())),
        "proof_id": receipt.proof_id,
        "protocol_sha256": receipt.protocol_sha256,
        "runtime_lock_sha256": receipt.runtime_lock_sha256,
        "status": observed.status,
        "terminal": observed.terminal,
    }
    if sha256(canonical_bytes(expected)).hexdigest() != receipt.receipt_sha256:
        raise ConformancePauseError(
            "conformance_proof_receipt_invalid", "proof receipt digest is invalid"
        )
    return receipt


def _canonical_document(data: bytes, code: str) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ConformancePauseError(code, "duplicate JSON key")
            result[key] = value
        return result

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicates)
        canonical = canonical_bytes(document)
    except (UnicodeDecodeError, json.JSONDecodeError, CanonicalizationError) as error:
        raise ConformancePauseError(code, "authority JSON is invalid") from error
    if not isinstance(document, dict) or canonical != data:
        raise ConformancePauseError(code, "authority JSON is not canonical")
    return document


def _hash_documents(documents: Mapping[str, object]) -> dict[str, str]:
    if not documents:
        raise ConformancePauseError("conformance_probe_inputs_missing", "proof inputs are empty")
    return {name: sha256(canonical_bytes(value)).hexdigest() for name, value in documents.items()}


def _validate_stage_locks(value: Mapping[str, object]) -> None:
    if set(value) != REQUIRED_STAGE_LOCKS:
        raise ConformancePauseError(
            "conformance_stage_locks_invalid", "stage lock inventory is invalid"
        )
    _validate_named_hashes(value, "conformance_stage_locks_invalid")


def _validate_named_hashes(value: Mapping[str, object], code: str) -> None:
    if not value or any(
        not isinstance(name, str) or not name or not _is_sha256(item)
        for name, item in value.items()
    ):
        raise ConformancePauseError(code, "hash mapping is invalid")


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConformancePauseError(code, "value must be a mapping")
    return value


def _nonempty_string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConformancePauseError(code, "value must be a nonempty string")
    return value


def _status(value: object) -> ProofStatus:
    if value not in {"passed", "failed", "unavailable", "interrupted"}:
        raise ConformancePauseError("conformance_proof_status_invalid", "invalid proof status")
    return value


def _validate_mode(value: object) -> None:
    if value not in {"unpaid_ci", "provider_qualification"}:
        raise ConformancePauseError("conformance_mode_invalid", "invalid conformance mode")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_sha256(value: object, code: str) -> str:
    if not _is_sha256(value):
        raise ConformancePauseError(code, "SHA-256 value is invalid")
    return value
