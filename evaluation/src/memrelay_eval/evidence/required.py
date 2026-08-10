"""Native evidence inventory and pre-grading authority gates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import ExecutionEvidenceConflictError, UnqualifiedEvidencePortError

REQUIRED_NATIVE_EVIDENCE_KINDS = frozenset(
    {
        "inspect_eval",
        "inspect_json",
        "sdk_events",
        "sdk_terminal",
        "workspace_patch",
        "usage",
        "limits",
        "cancellation",
        "typed_failure",
        "monotonic_active_agent_time",
        "provisioning_time",
        "queue_time",
        "backoff_time",
        "cleanup_time",
    }
)


@dataclass(frozen=True, slots=True)
class NativeEvidenceInventory:
    """All native terminal surfaces are separate hash-addressed artifacts."""

    artifacts: Mapping[str, ArtifactRef]

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", MappingProxyType(dict(self.artifacts)))

    @property
    def missing_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(REQUIRED_NATIVE_EVIDENCE_KINDS.difference(self.artifacts)))

    def require_complete(self) -> None:
        if self.missing_kinds:
            raise ExecutionEvidenceConflictError("native_evidence_incomplete")


def require_unpaid_conformance_ports(*ports: object) -> None:
    """Reject paid or unqualified authority before durable Epic 4 adapters exist."""

    for port in ports:
        if (
            getattr(port, "provenance", None) != "unpaid_conformance"
            or getattr(port, "eligible_for_paid_or_study", None) is not False
        ):
            raise UnqualifiedEvidencePortError(UnqualifiedEvidencePortError.code)
