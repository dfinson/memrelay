"""Immutable task-necessity, shortcut, contamination, and stability audit dispositions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from memrelay_eval.analysis.schemas import SAFETY_SCHEMA_VERSION
from memrelay_eval.canonical import attach_digest, canonical_digest
from memrelay_eval.domain.errors import SafetyAnalysisError

_AUDIT_KINDS: Final = frozenset(
    {
        "necessity",
        "shortcut",
        "duplicate",
        "canary",
        "holdout",
        "cutoff",
        "baseline_stability",
        "gold_stability",
    }
)
_REQUIRED_AUDITS: Final = frozenset(_AUDIT_KINDS)
_BLOCKING_REASONS: Final = {
    "shortcut": "SHORTCUT_MATERIAL",
    "duplicate": "DUPLICATE_THRESHOLD_BREACH",
    "canary": "CANARY_CONTAMINATION",
    "holdout": "HOLDOUT_ACCESS_BREACH",
    "cutoff": "CUTOFF_UNVERIFIED",
    "baseline_stability": "BASELINE_UNSTABLE",
    "gold_stability": "GOLD_UNSTABLE",
    "necessity": "NECESSITY_INSUFFICIENT",
}


@dataclass(frozen=True, slots=True)
class AuditPolicy:
    """Frozen audit rules and duplicate-comparison thresholds."""

    policy_id: str
    duplicate_threshold_sha256: str
    cutoff_policy_sha256: str
    grader_policy_sha256: str
    schema_version: str = SAFETY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.policy_id or self.schema_version != SAFETY_SCHEMA_VERSION:
            raise SafetyAnalysisError("audit_policy_invalid")
        for value in (
            self.duplicate_threshold_sha256,
            self.cutoff_policy_sha256,
            self.grader_policy_sha256,
        ):
            _require_sha256(value, "audit_policy_hash_invalid")

    @property
    def sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "duplicate_threshold_sha256": self.duplicate_threshold_sha256,
            "cutoff_policy_sha256": self.cutoff_policy_sha256,
            "grader_policy_sha256": self.grader_policy_sha256,
        }


@dataclass(frozen=True, slots=True)
class TaskAudit:
    """One source-authoritative audit; a task/kind may never be repaired in place."""

    task_id: str
    kind: str
    outcome: str
    policy_sha256: str
    evidence_sha256: tuple[str, ...]
    disposition_id: str

    def __post_init__(self) -> None:
        if not self.task_id or not self.disposition_id or self.kind not in _AUDIT_KINDS:
            raise SafetyAnalysisError("task_audit_identity_invalid")
        if self.outcome not in {"pass", "fail", "missing"}:
            raise SafetyAnalysisError("task_audit_outcome_invalid")
        _require_sha256(self.policy_sha256, "task_audit_policy_hash_invalid")
        _require_evidence(self.evidence_sha256, "task_audit_evidence_missing")


@dataclass(frozen=True, slots=True)
class TaskAuditDisposition:
    """Immutable task decision retaining every audit reason and evidence reference."""

    task_id: str
    status: str
    reasons: tuple[str, ...]
    policy_sha256: str
    audit_evidence_sha256: tuple[str, ...]
    prior_disposition_digest: str | None = None
    schema_version: str = SAFETY_SCHEMA_VERSION

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return attach_digest(
            {
                "schema_version": self.schema_version,
                "task_id": self.task_id,
                "status": self.status,
                "reasons": list(self.reasons),
                "policy_sha256": self.policy_sha256,
                "audit_evidence_sha256": list(self.audit_evidence_sha256),
                "prior_disposition_digest": self.prior_disposition_digest,
            }
        )


def materialize_task_audits(
    *,
    policy: AuditPolicy,
    audits: tuple[TaskAudit, ...],
    prior_dispositions: tuple[TaskAuditDisposition, ...] = (),
) -> tuple[TaskAuditDisposition, ...]:
    """Create immutable task dispositions; failures are quarantined, never repaired."""

    if not audits:
        raise SafetyAnalysisError("task_audits_missing")
    for audit in audits:
        if audit.policy_sha256 != policy.sha256:
            raise SafetyAnalysisError("task_audit_authority_conflict")
    _require_unique(
        ((audit.task_id, audit.kind) for audit in audits),
        "duplicate_task_audit_authority",
    )
    _require_unique(
        (item.task_id for item in prior_dispositions), "duplicate_prior_task_disposition"
    )
    grouped: dict[str, list[TaskAudit]] = {}
    for audit in audits:
        grouped.setdefault(audit.task_id, []).append(audit)
    prior_by_task = {item.task_id: item for item in prior_dispositions}
    return tuple(
        _task_disposition(policy, task_id, task_audits, prior_by_task.get(task_id))
        for task_id, task_audits in sorted(grouped.items())
    )


def _task_disposition(
    policy: AuditPolicy,
    task_id: str,
    audits: list[TaskAudit],
    prior: TaskAuditDisposition | None,
) -> TaskAuditDisposition:
    by_kind = {audit.kind: audit for audit in audits}
    reasons: set[str] = set()
    for kind in sorted(_REQUIRED_AUDITS - set(by_kind)):
        reasons.add(f"MISSING_{kind.upper()}_AUDIT")
    for kind, audit in by_kind.items():
        if audit.outcome == "missing":
            reasons.add(f"MISSING_{kind.upper()}_AUDIT")
        elif audit.outcome == "fail":
            reasons.add(_BLOCKING_REASONS[kind])
    if prior is not None and prior.status in {"quarantined", "rejected"}:
        reasons.add("SELECTIVE_REPAIR_FORBIDDEN")
    status = (
        "eligible"
        if not reasons
        else "rejected"
        if prior and prior.status == "rejected"
        else "quarantined"
    )
    if prior is not None:
        reasons.update(prior.reasons)
    evidence = tuple(
        sorted(
            {
                *(sha for audit in audits for sha in audit.evidence_sha256),
                *(() if prior is None else prior.audit_evidence_sha256),
            }
        )
    )
    return TaskAuditDisposition(
        task_id=task_id,
        status=status,
        reasons=tuple(sorted(reasons)),
        policy_sha256=policy.sha256,
        audit_evidence_sha256=evidence,
        prior_disposition_digest=None if prior is None else prior.digest,
    )


def _require_unique(values: object, code: str) -> None:
    materialized = tuple(values)  # type: ignore[arg-type]
    if len(materialized) != len(set(materialized)):
        raise SafetyAnalysisError(code)


def _require_evidence(values: tuple[str, ...], code: str) -> None:
    if not values or len(values) != len(set(values)):
        raise SafetyAnalysisError(code)
    for value in values:
        _require_sha256(value, code)


def _require_sha256(value: str, code: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or not value.isascii()
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SafetyAnalysisError(code)
