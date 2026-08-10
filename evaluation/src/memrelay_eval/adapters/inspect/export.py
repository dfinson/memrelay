"""Independent immutable evidence for Inspect authority and native corroboration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass

from memrelay_eval.adapters.inspect.task import NativeTerminalRecord
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import (
    ArtifactIntegrityError,
    ExecutionEvidenceConflictError,
    SecretBoundaryViolationError,
)
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.evidence.required import NativeEvidenceInventory
from memrelay_eval.evidence.secret_scan import SecretBoundaryScanner, SecretScanFinding


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    """References only: event bodies, patches, and prompts never enter the ledger."""

    inspect_state: str
    eval_artifact: ArtifactRef
    inspect_json_artifact: ArtifactRef
    native_terminal_artifact: ArtifactRef
    native_terminal: NativeTerminalRecord
    inventory: NativeEvidenceInventory


def persist_execution_evidence(
    store: ArtifactStorePort,
    *,
    inspect_state: str,
    eval_bytes: bytes,
    inspect_export: Mapping[str, object],
    native_terminal: NativeTerminalRecord,
    native_evidence: Mapping[str, object] | None = None,
    secret_boundaries: Mapping[str, object] | None = None,
) -> ExecutionEvidence:
    """Persist all authority records independently before any terminal decision."""

    payloads = _native_payloads(inspect_export, native_terminal, native_evidence)
    artifact_values: dict[str, tuple[object, str, str, str]] = {
        "inspect_eval": (
            eval_bytes,
            "inspect.eval",
            "application/x-inspect-eval",
            "execution_evidence",
        ),
        "inspect_json": (
            inspect_export,
            "inspect.export",
            "application/json",
            "execution_evidence",
        ),
        "sdk_terminal": (
            _terminal_projection(native_terminal),
            "native.terminal",
            "application/json",
            "execution_evidence",
        ),
    }
    for kind, payload in payloads.items():
        artifact_values[kind] = (
            payload,
            f"native.evidence.{kind}",
            "application/json",
            "execution_evidence",
        )

    findings: list[SecretScanFinding] = []
    artifacts: dict[str, ArtifactRef] = {}
    scanner = SecretBoundaryScanner()
    for kind, (value, location, media_type, classification) in artifact_values.items():
        safe_value, value_findings = _omit_unsafe_values(value, location, scanner)
        findings.extend(value_findings)
        artifact_bytes = (
            bytes(safe_value)
            if kind == "inspect_eval" and isinstance(safe_value, bytes)
            else canonical_bytes(safe_value)
        )
        artifacts[kind] = store.put_bytes(
            artifact_bytes,
            media_type=media_type,
            classification=classification,
        )

    if secret_boundaries:
        findings.extend(scanner.scan({"supplemental": secret_boundaries}))

    inventory = NativeEvidenceInventory(artifacts)
    inventory.require_complete()
    _verify_artifacts(store, inventory)
    if findings:
        typed_findings = tuple(findings)
        finding_ref = persist_secret_boundary_findings(store, typed_findings)
        raise SecretBoundaryViolationError(
            typed_findings,
            (*inventory.artifacts.values(), finding_ref),
        )
    evidence = ExecutionEvidence(
        inspect_state=inspect_state,
        eval_artifact=artifacts["inspect_eval"],
        inspect_json_artifact=artifacts["inspect_json"],
        native_terminal_artifact=artifacts["sdk_terminal"],
        native_terminal=native_terminal,
        inventory=inventory,
    )
    return evidence


def _omit_unsafe_values(
    value: object,
    location: str,
    scanner: SecretBoundaryScanner,
    depth: int = 0,
) -> tuple[object, tuple[SecretScanFinding, ...]]:
    if depth > 8:
        findings = scanner.scan({location: value})
        return _omission(findings), findings
    if isinstance(value, Mapping):
        safe: dict[str, object] = {}
        findings: list[SecretScanFinding] = []
        omitted_keys: list[dict[str, str]] = []
        for key, nested in value.items():
            key_text = str(key)
            key_location = f"{location}.{key_text}"
            key_findings = scanner.scan({f"{key_location}.name": key_text})
            if key_findings:
                findings.extend(key_findings)
                omitted_keys.extend(item.to_dict() for item in key_findings)
                continue
            pair_findings = ()
            if isinstance(nested, (str, bytes, bytearray)):
                pair_findings = scanner.scan({key_location: {key_text: nested}})
            if pair_findings:
                safe[key_text] = _omission(pair_findings)
                findings.extend(pair_findings)
                continue
            if isinstance(nested, (str, bytes, bytearray)):
                safe[key_text] = nested
                continue
            safe_nested, nested_findings = _omit_unsafe_values(
                nested, key_location, scanner, depth + 1
            )
            safe[key_text] = safe_nested
            findings.extend(nested_findings)
        if omitted_keys:
            safe["__memrelay_omitted_keys__"] = omitted_keys
        return safe, tuple(findings)
    if is_dataclass(value) and not isinstance(value, type):
        return _omit_unsafe_values(
            {item.name: getattr(value, item.name) for item in fields(value)},
            location,
            scanner,
            depth,
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        safe_items: list[object] = []
        findings: list[SecretScanFinding] = []
        for index, nested in enumerate(value):
            safe_nested, nested_findings = _omit_unsafe_values(
                nested, f"{location}[{index}]", scanner, depth + 1
            )
            safe_items.append(safe_nested)
            findings.extend(nested_findings)
        return safe_items, tuple(findings)
    value_findings = scanner.scan({location: value})
    if value_findings:
        return _omission(value_findings), value_findings
    return value, ()


def _omission(findings: tuple[SecretScanFinding, ...]) -> dict[str, object]:
    return {
        "status": "omitted",
        "code": _finding_terminal_code(findings),
        "findings": [finding.to_dict() for finding in findings],
    }


def _finding_terminal_code(findings: tuple[SecretScanFinding, ...]) -> str:
    if any(
        finding.detector.startswith("scan_")
        or finding.detector.endswith("_scan_failed")
        or finding.detector == "base64_scan_limit_exceeded"
        for finding in findings
    ):
        return "evidence_scan_failed"
    return SecretBoundaryViolationError.code


def reconcile_execution_evidence(
    evidence: ExecutionEvidence, inspect_export: Mapping[str, object]
) -> None:
    """Fail closed on missing or contradictory terminal authorities."""

    evidence.inventory.require_complete()
    if evidence.eval_artifact.size_bytes == 0:
        raise ExecutionEvidenceConflictError("Inspect .eval evidence is missing")
    if not evidence.native_terminal.corroborates_inspect:
        raise ExecutionEvidenceConflictError(
            "malformed native terminal cannot independently corroborate Inspect"
        )
    exported_state = inspect_export.get("status")
    if not isinstance(exported_state, str):
        raise ExecutionEvidenceConflictError("Inspect JSON export lacks terminal status")
    if exported_state != evidence.inspect_state or exported_state != evidence.native_terminal.state:
        raise ExecutionEvidenceConflictError("Inspect and native terminal records disagree")
    exported_usage = inspect_export.get("usage")
    if isinstance(exported_usage, Mapping):
        _require_equal_common_values(exported_usage, evidence.native_terminal.usage)
    _require_equal_common_values(
        inspect_export,
        evidence.native_terminal.usage,
        "retry_count",
        "cost",
        "total_tokens",
    )


def _native_payloads(
    inspect_export: Mapping[str, object],
    native_terminal: NativeTerminalRecord,
    supplied: Mapping[str, object] | None,
) -> dict[str, object]:
    payloads: dict[str, object] = {
        "sdk_events": native_terminal.raw_event_payload
        if native_terminal.raw_event_payload is not None
        else {"event_references": list(native_terminal.event_references)},
        "workspace_patch": {"patch_references": list(native_terminal.patch_references)},
        "usage": dict(native_terminal.usage),
        "limits": inspect_export.get("limits", {"unavailable": True}),
        "cancellation": inspect_export.get(
            "cancellation", {"cancelled": native_terminal.state == "cancelled"}
        ),
        "typed_failure": {"failure_code": native_terminal.failure_code},
        "monotonic_active_agent_time": {
            "seconds": native_terminal.usage.get("active_seconds", "unavailable")
        },
        "provisioning_time": inspect_export.get("provisioning_time", {"unavailable": True}),
        "queue_time": inspect_export.get("queue_time", {"unavailable": True}),
        "backoff_time": inspect_export.get("backoff_time", {"unavailable": True}),
        "cleanup_time": inspect_export.get("cleanup_time", {"unavailable": True}),
    }
    if supplied:
        protected = set(payloads).intersection(supplied)
        if protected:
            raise ExecutionEvidenceConflictError("native_authority_override_forbidden")
        payloads.update(supplied)
    return payloads


def _terminal_projection(native_terminal: NativeTerminalRecord) -> dict[str, object]:
    return {
        "state": native_terminal.state,
        "event_references": list(native_terminal.event_references),
        "patch_references": list(native_terminal.patch_references),
        "usage": dict(native_terminal.usage),
        "failure_code": native_terminal.failure_code,
    }


def _verify_artifacts(store: ArtifactStorePort, inventory: NativeEvidenceInventory) -> None:
    for artifact in inventory.artifacts.values():
        try:
            store.open_verified(artifact)
        except ArtifactIntegrityError as error:
            raise ExecutionEvidenceConflictError("native_artifact_integrity_failure") from error


def _require_equal_common_values(
    left: Mapping[str, object],
    right: Mapping[str, object],
    *keys: str,
) -> None:
    selected = keys or tuple(set(left).intersection(right))
    for key in selected:
        if key in left and key in right and left[key] != right[key]:
            raise ExecutionEvidenceConflictError("native_authority_value_disagreement")


def persist_secret_boundary_findings(
    store: ArtifactStorePort, findings: tuple[SecretScanFinding, ...]
) -> ArtifactRef:
    """Preserve only a canonical value-free finding projection when scanning blocks."""

    finding_bytes = canonical_bytes({"findings": [finding.to_dict() for finding in findings]})
    return store.put_bytes(
        finding_bytes,
        media_type="application/json",
        classification="secret_boundary_finding",
    )


def persist_execution_conflict_finding(store: ArtifactStorePort) -> ArtifactRef:
    """Persist a value-free execution-integrity failure for a terminal record."""

    return store.put_bytes(
        canonical_bytes({"code": ExecutionEvidenceConflictError.code}),
        media_type="application/json",
        classification="execution_evidence_conflict",
    )
