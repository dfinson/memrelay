"""Deterministic balance and operational diagnostics without post-hoc adjustment."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import AnalysisError

from .estimators import IttTable


@dataclass(frozen=True, slots=True)
class BalanceDiagnostic:
    field: str
    counts_by_arm: dict[str, dict[str, int]]

    def to_document(self) -> dict[str, object]:
        return {
            "field": self.field,
            "counts_by_arm": {
                arm: dict(sorted(counts.items()))
                for arm, counts in sorted(self.counts_by_arm.items())
            },
        }


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Immutable diagnostics; fingerprint and model-role strata remain separate."""

    source_itt_sha256: str
    diagnostics: tuple[BalanceDiagnostic, ...]
    fingerprint_model_role_strata: dict[str, int]

    @property
    def report_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "source_itt_sha256": self.source_itt_sha256,
            "diagnostics": [item.to_document() for item in self.diagnostics],
            "fingerprint_model_role_strata": dict(
                sorted(self.fingerprint_model_role_strata.items())
            ),
        }


def build_diagnostics(table: IttTable) -> DiagnosticReport:
    """Report design and post-assignment states, never use them to exclude observations."""
    if not table.observations:
        raise AnalysisError("itt_table_empty")
    fields = {
        "block_id": lambda item: item.disclosure.block_id,
        "cluster_id": lambda item: item.disclosure.cluster_id,
        "run_order": lambda item: str(item.disclosure.run_order),
        "concurrency_slot": lambda item: str(item.disclosure.concurrency_slot),
        "environment_fingerprint_sha256": lambda item: item.environment_fingerprint_sha256,
        "model_role": lambda item: item.disclosure.model_role,
        "task_id": lambda item: item.task_id,
        "sequence_id": lambda item: item.sequence_id,
        "repository_id": lambda item: item.repository_id,
        "quota_state": lambda item: str(item.disclosure.quota_state),
        "throttle_state": lambda item: str(item.disclosure.throttle_state),
        "provider_time_bucket": lambda item: str(item.disclosure.provider_time_bucket),
        "exposure_status": lambda item: item.exposure_status,
        "attrition_status": lambda item: item.attrition_status,
        "contamination_status": lambda item: item.contamination_status,
        "terminal_kind": lambda item: item.terminal_kind,
        "outcome_status": lambda item: item.outcome_status,
    }
    diagnostics = tuple(
        BalanceDiagnostic(
            field=field,
            counts_by_arm={
                arm: dict(Counter(value(item) for item in table.observations if item.arm == arm))
                for arm in (table.estimand.control_arm, table.estimand.treatment_arm)
            },
        )
        for field, value in fields.items()
    )
    strata: Counter[str] = Counter(
        f"{item.environment_fingerprint_sha256}:{item.disclosure.model_role}"
        for item in table.observations
    )
    return DiagnosticReport(table.table_sha256, diagnostics, dict(strata))
