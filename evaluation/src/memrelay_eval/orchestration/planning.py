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
import re
import socket
import unicodedata
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from memrelay_eval.adapters.fakes import (
    FakeCopilotPort,
    FakeMemrelayPort,
    FakeOpenAIPort,
    InMemoryArtifactStore,
    InMemoryLedger,
    InMemoryTelemetry,
)
from memrelay_eval.canonical import attach_digest, canonical_bytes, canonical_digest
from memrelay_eval.catalog.compiler import (
    _write_bytes_durable,
    compile_catalog_command,
)
from memrelay_eval.domain.entities import TelemetryObservation
from memrelay_eval.domain.errors import DomainError
from memrelay_eval.domain.ids import ExperimentId, ProtocolId, RunId
from memrelay_eval.domain.states import RunState
from memrelay_eval.orchestration.assignment import (
    AssignmentAlgorithmRegistry,
    FixtureBalancedBlockAlgorithm,
)

PlanTerminalStatus = Literal["succeeded", "failed", "interrupted", "cancelled"]

# Evidence labels that are never permitted on planning output.
_PROHIBITED_LABELS = frozenset(
    {
        "study",
        "included",
        "efficacy",
        "safety",
        "economics",
        "release_fitness",
        "product_efficacy",
        "release_gate",
    }
)

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
_SAFE_SCHEMA_KEYS = frozenset({"error_code", "exit_code"})
# Detection-only skeleton for reviewed Latin lookalikes relevant to redaction terms.
_REDACTION_CONFUSABLES = str.maketrans(
    {
        "\u03b1": "a",  # Greek small alpha
        "\u0430": "a",  # Cyrillic small a
        "\u03f2": "c",  # Greek small lunate sigma
        "\u0441": "c",  # Cyrillic small es
        "\u0435": "e",  # Cyrillic small ie
        "\u0456": "i",  # Cyrillic small byelorussian-ukrainian i
        "\u03b9": "i",  # Greek small iota
        "\u043c": "m",  # Cyrillic small em
        "\u03bf": "o",  # Greek small omicron
        "\u043e": "o",  # Cyrillic small o
        "\u03c1": "p",  # Greek small rho
        "\u0440": "p",  # Cyrillic small er
        "\u03c3": "s",  # Greek small sigma
        "\u03c2": "s",  # Greek small final sigma
        "\u0455": "s",  # Cyrillic small dze
        "\u03c4": "t",  # Greek small tau
        "\u0442": "t",  # Cyrillic small te
        "\u0443": "y",  # Cyrillic small u
    }
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


@dataclass(frozen=True, slots=True)
class OfflinePlanningPorts:
    """All unpaid ports composed into an offline planning execution."""

    copilot: FakeCopilotPort
    openai: FakeOpenAIPort
    memrelay: FakeMemrelayPort
    artifacts: InMemoryArtifactStore
    ledger: InMemoryLedger
    telemetry: InMemoryTelemetry

    @classmethod
    def fake(cls) -> OfflinePlanningPorts:
        return cls(
            copilot=FakeCopilotPort(),
            openai=FakeOpenAIPort(),
            memrelay=FakeMemrelayPort(),
            artifacts=InMemoryArtifactStore(),
            ledger=InMemoryLedger(),
            telemetry=InMemoryTelemetry(),
        )


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
    if label.strip().casefold() in _PROHIBITED_LABELS:
        raise EvidenceLabelError(label)
    return label


def redaction_scan(data: bytes, *, context: str = "") -> None:
    """Raise if emitted text carries a prohibited term without false schema matches."""
    del context
    text = data.decode("utf-8", errors="replace")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        _raise_if_redacted_text(text)
        return
    _scan_redacted_value(document)


def _scan_redacted_value(value: object) -> None:
    if isinstance(value, str):
        _raise_if_redacted_text(value)
    elif isinstance(value, list):
        for item in value:
            _scan_redacted_value(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text not in _SAFE_SCHEMA_KEYS:
                _raise_if_redacted_text(key_text)
            _scan_redacted_value(item)


def _raise_if_redacted_text(text: str) -> None:
    normalized = _redaction_match_text(text)
    for term in _REDACTED_TERMS:
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", normalized):
            raise RedactionViolationError(term)


def _redaction_match_text(text: str) -> str:
    """Return a detection-only skeleton without changing emitted manifest bytes.

    NFKD exposes decomposed marks, which are removed with default-ignorables
    before casefolding into the reviewed lowercase confusable skeleton.
    """
    normalized = unicodedata.normalize("NFKD", text)
    visible = "".join(
        character
        for character in normalized
        if not _is_default_ignorable(character)
        and not unicodedata.category(character).startswith("M")
    )
    return visible.casefold().translate(_REDACTION_CONFUSABLES)


def _is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) == "Cf"
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


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
    ports: OfflinePlanningPorts | None = None,
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
    ports = ports or OfflinePlanningPorts.fake()
    planned_manifest_path = output_dir / "planned-run-manifest.json"
    prior_planned_manifest = (
        planned_manifest_path.read_bytes() if planned_manifest_path.is_file() else None
    )

    with network_deny_guard():
        try:
            result = _execute_planning_pipeline(
                catalog_path=catalog_path,
                output_dir=output_dir,
                manifest_path=manifest_path,
                lock_path=lock_path,
                prior_lock=prior_lock,
                runtime_lock=runtime_lock,
                seed=seed,
                seed_commitment=seed_commitment,
                evidence_classification=evidence_classification,
                ports=ports,
            )
        except KeyboardInterrupt:
            _restore_prior_planned_manifest(planned_manifest_path, prior_planned_manifest)
            result = PlanningResult(
                terminal_status="interrupted",
                exit_code=130,
                evidence_classification=evidence_classification,
                error_code="keyboard_interrupt",
            )
        except PlanningError as error:
            _restore_prior_planned_manifest(planned_manifest_path, prior_planned_manifest)
            result = PlanningResult(
                terminal_status="failed",
                exit_code=3,
                evidence_classification=evidence_classification,
                error_code=error.code,
                error_message=str(error),
            )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes_durable(manifest_path, plan_offline_to_command_manifest(result))
    return result


def _restore_prior_planned_manifest(path: Path, prior_bytes: bytes | None) -> None:
    if prior_bytes is not None:
        _write_bytes_durable(path, prior_bytes)


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
    ports: OfflinePlanningPorts,
) -> PlanningResult:
    """Internal pipeline; all exceptions propagate to plan_offline."""

    # Phase 1: Compile the catalog
    # The compiler requires its output and lock to remain under the catalog root.
    catalog_root = catalog_path.parent
    if not _is_child(output_dir, catalog_root):
        raise PlanningError("invalid_output_dir_layout")
    effective_lock_path = lock_path or catalog_root / "catalog-lock.json"
    if not _is_child(effective_lock_path, catalog_root):
        raise PlanningError("invalid_lock_path_layout")
    effective_manifest_path = catalog_root / "plan-compile-manifest.json"
    planned_manifest_path = output_dir / "planned-run-manifest.json"
    reserved_paths = {
        catalog_path.resolve(),
        effective_lock_path.resolve(),
        effective_manifest_path.resolve(),
        planned_manifest_path.resolve(),
    }
    if manifest_path.resolve() in reserved_paths:
        raise PlanningError("command_manifest_path_conflict")
    output_dir.mkdir(parents=True, exist_ok=True)
    _validate_offline_ports(ports)
    compile_result = compile_catalog_command(
        catalog_path,
        output_dir=output_dir,
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

    # Phase 3: Use the injected fake ports; none can reach a real provider.
    artifact_store = ports.artifacts
    ledger = ports.ledger

    # Phase 4: Create assignment infrastructure
    algorithm = FixtureBalancedBlockAlgorithm(
        name="fixture-balanced-block",
        version="1.0.0",
        slot_count=2,
    )
    registry = AssignmentAlgorithmRegistry([algorithm])
    algorithm = registry.require(algorithm.name, algorithm.version)

    # Build a minimal assignment plan document
    protocol_id = compilation.protocol_ids[0] if compilation.protocol_ids else ProtocolId.new()
    experiment_id = ExperimentId.from_digest(
        canonical_digest(
            {"catalog_hash": compilation.catalog_input_sha256, "protocol": str(protocol_id)}
        )
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
        canonical_digest(
            {
                "experiment_id": str(experiment_id),
                "assignment_plan_hash": assignment_plan_hash,
                "catalog_input": compilation.catalog_input_sha256,
            }
        )
    )
    if ledger.history(run_id):
        raise PlanningError("unexpected_lifecycle_transition")
    ports.telemetry.emit(
        TelemetryObservation(
            "offline_planning_composed",
            datetime(1970, 1, 1, tzinfo=UTC),
            {"unpaid_conformance": True},
        )
    )

    planned_manifest_document = attach_digest(
        {
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
            "runtime_lock": (dict(compilation.runtime_lock) if compilation.runtime_lock else None),
            "compilation_result": {
                "change_kind": compilation.change_kind,
                "recovery_status": compilation.recovery_status,
                "protocol_ids": list(compilation.protocol_ids),
            },
        }
    )

    planned_manifest_bytes = canonical_bytes(planned_manifest_document)

    # Redaction scan on the final manifest
    redaction_scan(planned_manifest_bytes, context="planned_run_manifest")

    manifest_sha256 = sha256(planned_manifest_bytes).hexdigest()

    output_hashes: dict[str, str] = {
        "planned_manifest": manifest_sha256,
        "assignment_plan": assignment_plan_hash,
    }

    # Publish the planned-run manifest through the compiler's durable atomic path.
    _write_bytes_durable(planned_manifest_path, planned_manifest_bytes)

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


def _validate_offline_ports(ports: OfflinePlanningPorts) -> None:
    if not isinstance(ports.copilot, FakeCopilotPort):
        raise PlanningError("offline_copilot_port_required")
    if not isinstance(ports.openai, FakeOpenAIPort):
        raise PlanningError("offline_openai_port_required")
    if not isinstance(ports.memrelay, FakeMemrelayPort):
        raise PlanningError("offline_memrelay_port_required")
    for port in (
        ports.copilot,
        ports.openai,
        ports.memrelay,
        ports.artifacts,
        ports.ledger,
        ports.telemetry,
    ):
        if port.provenance != "unpaid_conformance" or port.eligible_for_paid_or_study:
            raise PlanningError("offline_port_provenance_required")


def plan_offline_to_command_manifest(result: PlanningResult) -> bytes:
    """Serialize a PlanningResult as a redacted, typed command manifest."""
    document = attach_digest(
        {
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
        }
    )
    manifest_bytes = canonical_bytes(document)
    redaction_scan(manifest_bytes, context="command_manifest")
    return manifest_bytes
