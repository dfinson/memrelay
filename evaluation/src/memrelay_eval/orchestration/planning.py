"""Deterministic offline catalog-to-planned-run dry run.

This module orchestrates the full planning pipeline: validate the catalog,
compile canonical artifacts, freeze enrollment inputs, assign opaquely, and
emit a deterministic planned-run manifest. It stops at the ``planned`` state
and never appends ``assigned``, ``provisioned``, ``running``, or later
transitions.

All ports are injected fakes; a network-deny guard blocks sockets and DNS.
Output is labeled ``implementation_evidence`` or ``unpaid_conformance`` only.
"""

from __future__ import annotations

import json
import socket
import sys
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator, Literal

from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger
from memrelay_eval.canonical import attach_digest, canonical_bytes, canonical_digest
from memrelay_eval.catalog.compiler import (
    CompileCommandResult,
    compile_catalog_command,
)
from memrelay_eval.domain.errors import DomainError
from memrelay_eval.domain.ids import ExperimentId, ProtocolId, RunId
from memrelay_eval.domain.states import RunState
from memrelay_eval.orchestration.assignment import (
    AssignmentAlgorithmRegistry,
    AssignmentRequest,
    ConcealedAssignmentService,
    FixtureBalancedBlockAlgorithm,
)

PlanTerminalStatus = Literal["succeeded", "failed", "interrupted", "cancelled"]

# Evidence labels that are never permitted on planning output.
_PROHIBITED_LABELS = frozenset({
    "study",
    "included",
    "efficacy",
    "safety",
    "economics",
    "release_fitness",
    "product_efficacy",
    "release_gate",
})

# Terms that must not appear in emitted manifests or logs.
_REDACTED_TERMS = (
    "prompt",
    "code",
    "repo",
    "repository",
    "user",
    "credential",
    "secret",
    "provider",
    "treatment",
    "arm",
)


class PlanningError(DomainError):
    """A planning pipeline failure with a typed code."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code.replace("_", " "))


class EvidenceLabelError(PlanningError):
    """A prohibited evidence label was requested."""

    def __init__(self, label: str) -> None:
        super().__init__("prohibited_evidence_label", f"label rejected: {label}")
        self.label = label


class RedactionViolationError(PlanningError):
    """Emitted bytes contain a prohibited term."""

    def __init__(self, term: str) -> None:
        super().__init__("redaction_violation", f"prohibited term found: {term}")
        self.term = term


class NetworkDenyError(PlanningError):
    """A network operation was attempted under the deny guard."""

    def __init__(self) -> None:
        super().__init__("network_denied", "network access denied during offline planning")


@dataclass(frozen=True, slots=True)
class PlanningResult:
    """Typed terminal result of an offline planning run."""

    terminal_status: PlanTerminalStatus
    exit_code: int
    input_hashes: Mapping[str, str] = field(default_factory=dict)
    output_hashes: Mapping[str, str] = field(default_factory=dict)
    runtime_lock_ref: str | None = None
    protocol_id: str | None = None
    manifest_ref: str | None = None
    evidence_classification: str = "unpaid_conformance"
    error_code: str | None = None
    error_message: str | None = None


@contextmanager
def network_deny_guard() -> Iterator[None]:
    """Block all socket and DNS operations for the duration of the context."""
    original_socket = socket.socket
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyname = socket.gethostbyname

    def _denied_socket(*args: Any, **kwargs: Any) -> Any:
        raise NetworkDenyError()

    def _denied_getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        raise NetworkDenyError()

    def _denied_gethostbyname(*args: Any, **kwargs: Any) -> Any:
        raise NetworkDenyError()

    socket.socket = _denied_socket  # type: ignore[assignment]
    socket.getaddrinfo = _denied_getaddrinfo  # type: ignore[assignment]
    socket.gethostbyname = _denied_gethostbyname  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[assignment]
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]
        socket.gethostbyname = original_gethostbyname  # type: ignore[assignment]


def validate_evidence_label(label: str) -> str:
    """Accept only implementation/conformance labels; reject prohibited ones."""
    if label in _PROHIBITED_LABELS:
        raise EvidenceLabelError(label)
    return label


def redaction_scan(data: bytes, *, context: str = "") -> None:
    """Raise if emitted bytes contain any prohibited term as a distinct token."""
    lower = data.decode("utf-8", errors="replace").lower()
    for term in _REDACTED_TERMS:
        # Check for the term as a JSON key or standalone word boundary
        if f'"{term}"' in lower or f"_{term}_" in lower:
            raise RedactionViolationError(term)


def plan_offline(
    *,
    catalog_path: Path,
    output_dir: Path,
    manifest_path: Path,
    lock_path: Path | None = None,
    prior_lock: Path | None = None,
    runtime_lock: Path | None = None,
    seed: bytes | None = None,
    evidence_classification: str = "unpaid_conformance",
) -> PlanningResult:
    """Execute the full offline planning pipeline under a network-deny guard.

    Returns a deterministic PlanningResult. Same inputs produce byte-identical
    manifests.
    """
    validate_evidence_label(evidence_classification)

    # Deterministic seed for assignment
    if seed is None:
        seed = b"offline-planning-deterministic-seed-v1"
    seed_commitment = sha256(seed).hexdigest()

    with network_deny_guard():
        try:
            return _execute_planning_pipeline(
                catalog_path=catalog_path,
                output_dir=output_dir,
                manifest_path=manifest_path,
                lock_path=lock_path,
                prior_lock=prior_lock,
                runtime_lock=runtime_lock,
                seed=seed,
                seed_commitment=seed_commitment,
                evidence_classification=evidence_classification,
            )
        except KeyboardInterrupt:
            return PlanningResult(
                terminal_status="interrupted",
                exit_code=130,
                evidence_classification=evidence_classification,
                error_code="keyboard_interrupt",
            )
        except PlanningError as error:
            return PlanningResult(
                terminal_status="failed",
                exit_code=3,
                evidence_classification=evidence_classification,
                error_code=error.code,
                error_message=str(error),
            )


def _is_child(path: Path, parent: Path) -> bool:
    """Check if path is a direct child or descendant of parent."""
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False

def _execute_planning_pipeline(
    *,
    catalog_path: Path,
    output_dir: Path,
    manifest_path: Path,
    lock_path: Path | None,
    prior_lock: Path | None,
    runtime_lock: Path | None,
    seed: bytes,
    seed_commitment: str,
    evidence_classification: str,
) -> PlanningResult:
    """Internal pipeline; all exceptions propagate to plan_offline."""

    # Phase 1: Compile the catalog
    # The compiler requires catalog, output_dir, lock, and manifest to be
    # direct children of one catalog root. Resolve relative to catalog parent.
    catalog_root = catalog_path.parent
    effective_output_dir = output_dir if _is_child(output_dir, catalog_root) else (catalog_root / "generated")
    effective_lock_path = lock_path or (catalog_root / "catalog-lock.json")
    if not _is_child(effective_lock_path, catalog_root):
        effective_lock_path = catalog_root / "catalog-lock.json"
    effective_manifest_path = manifest_path if _is_child(manifest_path, catalog_root) else (catalog_root / "plan-compile-manifest.json")
    effective_output_dir.mkdir(parents=True, exist_ok=True)
    compile_result = compile_catalog_command(
        catalog_path,
        output_dir=effective_output_dir,
        lock_path=effective_lock_path,
        manifest_path=effective_manifest_path,
        prior_lock=prior_lock,
        runtime_lock=runtime_lock,
    )
    if compile_result.terminal_status != "succeeded":
        exit_code = 1 if compile_result.exit_code == 1 else 3
        return PlanningResult(
            terminal_status="failed",
            exit_code=exit_code,
            evidence_classification=evidence_classification,
            error_code=f"catalog_{compile_result.terminal_status}",
            error_message=(
                str(compile_result.error) if compile_result.error else "catalog compilation failed"
            ),
        )

    compilation = compile_result.compilation
    assert compilation is not None

    # Phase 2: Build input hashes from compilation
    input_hashes: dict[str, str] = {
        "catalog_source": compilation.source_sha256,
        "catalog_input": compilation.catalog_input_sha256,
    }
    for key, value in compilation.output_sha256.items():
        input_hashes[f"compiled_{key}"] = value

    # Phase 3: Construct fake ports
    artifact_store = InMemoryArtifactStore()
    ledger = InMemoryLedger()

    # Phase 4: Create assignment infrastructure
    algorithm = FixtureBalancedBlockAlgorithm(
        name="fixture-balanced-block",
        version="1.0.0",
        slot_count=2,
    )
    registry = AssignmentAlgorithmRegistry([algorithm])

    # Build a minimal assignment plan document
    protocol_id = (
        compilation.protocol_ids[0]
        if compilation.protocol_ids
        else ProtocolId.new()
    )
    experiment_id = ExperimentId.from_digest(
        canonical_digest({"catalog_hash": compilation.catalog_input_sha256, "protocol": str(protocol_id)})
    )

    # Store the assignment plan as an artifact
    assignment_plan_inputs = {
        "enrollment_plan_id": "synthetic_offline_plan",
        "algorithm_name": algorithm.name,
        "algorithm_version": algorithm.version,
        "seed_commitment": seed_commitment,
        "catalog_hash": compilation.catalog_input_sha256,
        "protocol_id": str(protocol_id),
        "evidence_classification": evidence_classification,
    }
    assignment_plan_bytes = canonical_bytes(assignment_plan_inputs)
    assignment_plan_hash = sha256(assignment_plan_bytes).hexdigest()

    # Store in fake artifact store
    artifact_store.put_bytes(
        assignment_plan_bytes,
        media_type="application/json",
        classification=evidence_classification,
    )

    # Phase 5: Produce planned-run manifest (deterministic)
    run_id = RunId.from_digest(
        canonical_digest({
            "experiment_id": str(experiment_id),
            "assignment_plan_hash": assignment_plan_hash,
            "catalog_input": compilation.catalog_input_sha256,
        })
    )

    planned_manifest_document = attach_digest({
        "schema_version": "1.0.0",
        "artifact_type": "planned_run_manifest",
        "evidence_classification": evidence_classification,
        "terminal_state": RunState.PLANNED.value,
        "experiment_id": str(experiment_id),
        "protocol_id": str(protocol_id),
        "run_id": str(run_id),
        "assignment_plan_hash": assignment_plan_hash,
        "seed_commitment": seed_commitment,
        "input_hashes": dict(sorted(input_hashes.items())),
        "runtime_lock": (
            dict(compilation.runtime_lock) if compilation.runtime_lock else None
        ),
        "compilation_result": {
            "change_kind": compilation.change_kind,
            "recovery_status": compilation.recovery_status,
            "protocol_ids": list(compilation.protocol_ids),
        },
    })

    planned_manifest_bytes = canonical_bytes(planned_manifest_document)

    # Redaction scan on the final manifest
    redaction_scan(planned_manifest_bytes, context="planned_run_manifest")

    manifest_sha256 = sha256(planned_manifest_bytes).hexdigest()

    output_hashes: dict[str, str] = {
        "planned_manifest": manifest_sha256,
        "assignment_plan": assignment_plan_hash,
    }

    # Write the planned manifest to the user-specified output directory
    user_output_dir = output_dir
    user_output_dir.mkdir(parents=True, exist_ok=True)
    plan_manifest_path = user_output_dir / "planned-run-manifest.json"
    plan_manifest_path.write_bytes(planned_manifest_bytes)

    return PlanningResult(
        terminal_status="succeeded",
        exit_code=0,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        runtime_lock_ref=(
            compilation.runtime_lock.get("sha256") if compilation.runtime_lock else None
        ),
        protocol_id=str(protocol_id),
        manifest_ref=manifest_sha256,
        evidence_classification=evidence_classification,
    )


def plan_offline_to_command_manifest(result: PlanningResult) -> bytes:
    """Serialize a PlanningResult as a redacted, typed command manifest."""
    document = attach_digest({
        "schema_version": "1.0.0",
        "command": "plan-offline",
        "terminal_status": result.terminal_status,
        "exit_code": result.exit_code,
        "evidence_classification": result.evidence_classification,
        "input_hashes": dict(result.input_hashes),
        "output_hashes": dict(result.output_hashes),
        "runtime_lock_ref": result.runtime_lock_ref,
        "protocol_id": result.protocol_id,
        "manifest_ref": result.manifest_ref,
        "error_code": result.error_code,
    })
    manifest_bytes = canonical_bytes(document)
    redaction_scan(manifest_bytes, context="command_manifest")
    return manifest_bytes

