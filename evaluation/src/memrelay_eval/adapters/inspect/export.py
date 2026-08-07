"""Independent immutable evidence for Inspect authority and native corroboration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from memrelay_eval.adapters.inspect.task import NativeTerminalRecord
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import ExecutionEvidenceConflictError
from memrelay_eval.domain.ports import ArtifactStorePort


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    """References only: event bodies, patches, and prompts never enter the ledger."""

    inspect_state: str
    eval_artifact: ArtifactRef
    inspect_json_artifact: ArtifactRef
    native_terminal_artifact: ArtifactRef
    native_terminal: NativeTerminalRecord


def persist_execution_evidence(
    store: ArtifactStorePort,
    *,
    inspect_state: str,
    eval_bytes: bytes,
    inspect_export: Mapping[str, object],
    native_terminal: NativeTerminalRecord,
) -> ExecutionEvidence:
    """Persist all authority records independently before any terminal decision."""

    export_bytes = canonical_bytes(dict(inspect_export))
    native_bytes = canonical_bytes(
        {
            "state": native_terminal.state,
            "event_references": list(native_terminal.event_references),
            "patch_references": list(native_terminal.patch_references),
            "usage": dict(native_terminal.usage),
            "failure_code": native_terminal.failure_code,
        }
    )
    evidence = ExecutionEvidence(
        inspect_state=inspect_state,
        eval_artifact=store.put_bytes(
            bytes(eval_bytes),
            media_type="application/x-inspect-eval",
            classification="execution_evidence",
        ),
        inspect_json_artifact=store.put_bytes(
            export_bytes, media_type="application/json", classification="execution_evidence"
        ),
        native_terminal_artifact=store.put_bytes(
            native_bytes, media_type="application/json", classification="execution_evidence"
        ),
        native_terminal=native_terminal,
    )
    return evidence


def reconcile_execution_evidence(
    evidence: ExecutionEvidence, inspect_export: Mapping[str, object]
) -> None:
    """Fail closed on missing or contradictory terminal authorities."""

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
