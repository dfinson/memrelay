"""Deterministic local projections of immutable evaluator evidence."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.errors import AnalysisError

from .claims import BoundedClaim, ClaimScope, bound_claim
from .gates import ClaimGateDecision, ReleaseFitnessDecision

_REQUIRED_SECTIONS = (
    "simultaneous_intervals",
    "marginal_descriptive_intervals",
    "diagnostics",
    "pareto_surfaces",
    "harm_tails",
    "safety",
    "costs",
    "time",
    "panel_metrics",
    "gates",
)
REPORT_RENDERER_VERSION = "1.0.0"
REPORT_TEMPLATE_SHA256 = sha256(b"evidence-linked-report-markdown-v1").hexdigest()
_REPORT_SOURCE_KINDS = frozenset(
    {
        "completed_reconciled_product",
        "construction",
        "component_test",
        "deterministic_fixture",
        "engine_upper_bound",
        "pilot",
    }
)


@dataclass(frozen=True, slots=True)
class ReportInput:
    """All report inputs are immutable authority references, never latest aliases."""

    report_id: str
    stage: str
    scope: ClaimScope
    dataset_manifest_sha256: str
    table_sha256: tuple[str, ...]
    figure_sha256: tuple[str, ...]
    estimator_sha256: str
    interval_sha256: tuple[str, ...]
    family_sha256: str
    power_sha256: str
    safety_sha256: str
    panel_sha256: str
    cost_revision_sha256: str
    runtime_lock_sha256: str
    template_sha256: str
    gate_ids: tuple[str, ...]
    claim_decisions: tuple[ClaimGateDecision, ...]
    release_fitness: ReleaseFitnessDecision
    source_kind: str
    reproduction_status: str
    sections: Mapping[str, tuple[Mapping[str, object], ...]]

    def __post_init__(self) -> None:
        if not self.report_id or not self.stage:
            raise AnalysisError("report_identity_missing")
        hashes = (
            self.dataset_manifest_sha256,
            self.estimator_sha256,
            self.family_sha256,
            self.power_sha256,
            self.safety_sha256,
            self.panel_sha256,
            self.cost_revision_sha256,
            self.runtime_lock_sha256,
            self.template_sha256,
            *self.table_sha256,
            *self.figure_sha256,
            *self.interval_sha256,
        )
        if not hashes or any(not _is_sha256(value) for value in hashes):
            raise AnalysisError("report_input_lineage_invalid")
        if not self.gate_ids or any(not item for item in self.gate_ids):
            raise AnalysisError("report_gate_ids_missing")
        if self.template_sha256 != REPORT_TEMPLATE_SHA256:
            raise AnalysisError("report_template_drift")
        if self.source_kind == "unreconciled_trial":
            raise AnalysisError("report_unreconciled_input_forbidden")
        if self.source_kind not in _REPORT_SOURCE_KINDS:
            raise AnalysisError("report_source_kind_invalid")
        if self.reproduction_status not in {"verified", "pending", "failed"}:
            raise AnalysisError("report_reproduction_status_invalid")
        if not self.claim_decisions:
            raise AnalysisError("report_claim_decisions_missing")
        if (
            self.release_fitness.protocol_sha256 != self.scope.protocol_sha256
            or self.release_fitness.family_sha256 != self.family_sha256
            or self.release_fitness.derivation_sha256 != self.scope.derivation_sha256
            or self.release_fitness.source_sha256 != self.scope.source_sha256
            or self.release_fitness.reproduction_status != self.reproduction_status
        ):
            raise AnalysisError("report_release_fitness_lineage_conflict")
        if (
            self.source_kind != "completed_reconciled_product"
            or self.reproduction_status != "verified"
        ) and self.release_fitness.status != "draft/unverified":
            raise AnalysisError("report_release_fitness_promotion_forbidden")
        if any(
            decision.protocol_sha256 != self.scope.protocol_sha256
            or decision.source_sha256 not in self.scope.source_sha256
            or decision.derivation_sha256 != self.scope.derivation_sha256
            or decision.endpoint_id != self.scope.endpoint_id
            for decision in self.claim_decisions
        ):
            raise AnalysisError("report_claim_lineage_conflict")
        if set(self.sections) != set(_REQUIRED_SECTIONS):
            raise AnalysisError("report_sections_incomplete")
        for name, values in self.sections.items():
            if not values or any(not isinstance(value, Mapping) for value in values):
                raise AnalysisError("report_section_evidence_missing", (name,))
            for value in values:
                if (
                    tuple(value.get("source_sha256", ())) != self.scope.source_sha256
                    or value.get("derivation_sha256") != self.scope.derivation_sha256
                    or tuple(value.get("evidence_ids", ())) != self.scope.evidence_ids
                    or value.get("protocol_sha256") != self.scope.protocol_sha256
                    or value.get("population_id") != self.scope.population_id
                    or value.get("model_id") != self.scope.model_id
                    or value.get("endpoint_id") != self.scope.endpoint_id
                    or value.get("stratum") != self.scope.stratum
                    or value.get("history_regime") != self.scope.history_regime
                    or value.get("environment_sha256") != self.scope.environment_sha256
                ):
                    raise AnalysisError("report_item_lineage_conflict", (name,))

    @property
    def input_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "frozen_report_input",
            "report_id": self.report_id,
            "stage": self.stage,
            "scope": self.scope.to_document(),
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "table_sha256": list(self.table_sha256),
            "figure_sha256": list(self.figure_sha256),
            "estimator_sha256": self.estimator_sha256,
            "interval_sha256": list(self.interval_sha256),
            "family_sha256": self.family_sha256,
            "power_sha256": self.power_sha256,
            "safety_sha256": self.safety_sha256,
            "panel_sha256": self.panel_sha256,
            "cost_revision_sha256": self.cost_revision_sha256,
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "template_sha256": self.template_sha256,
            "gate_ids": sorted(self.gate_ids),
            "claim_decisions": [item.to_document() for item in self.claim_decisions],
            "release_fitness": self.release_fitness.to_document(),
            "source_kind": self.source_kind,
            "reproduction_status": self.reproduction_status,
            "sections": {name: list(self.sections[name]) for name in sorted(self.sections)},
        }


@dataclass(frozen=True, slots=True)
class EvidenceLinkedReport:
    report_input: ReportInput
    claims: tuple[BoundedClaim, ...]
    terminal_status: str

    def __post_init__(self) -> None:
        if self.terminal_status not in {"verified", "draft/unverified"}:
            raise AnalysisError("report_terminal_status_invalid")
        if not self.claims:
            raise AnalysisError("report_claims_missing")

    @property
    def report_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "evidence_linked_report",
            "report_input_sha256": self.report_input.input_sha256,
            "report_id": self.report_input.report_id,
            "stage": self.report_input.stage,
            "terminal_status": self.terminal_status,
            "scope": self.report_input.scope.to_document(),
            "input_hashes": {
                "dataset_manifest_sha256": self.report_input.dataset_manifest_sha256,
                "table_sha256": list(self.report_input.table_sha256),
                "figure_sha256": list(self.report_input.figure_sha256),
                "estimator_sha256": self.report_input.estimator_sha256,
                "interval_sha256": list(self.report_input.interval_sha256),
                "family_sha256": self.report_input.family_sha256,
                "power_sha256": self.report_input.power_sha256,
                "safety_sha256": self.report_input.safety_sha256,
                "panel_sha256": self.report_input.panel_sha256,
                "cost_revision_sha256": self.report_input.cost_revision_sha256,
            },
            "sections": {
                name: list(self.report_input.sections[name]) for name in _REQUIRED_SECTIONS
            },
            "claims": [item.to_document() for item in self.claims],
            "release_fitness": self.report_input.release_fitness.to_document(),
        }


def render_report(report_input: ReportInput) -> EvidenceLinkedReport:
    """Render immutable evidence without reanalysing data or selecting a favorable view."""
    claims = tuple(
        bound_claim(
            decision,
            report_input.scope,
            source_kind=report_input.source_kind,
            reproduction_status=report_input.reproduction_status,
        )
        for decision in report_input.claim_decisions
    )
    status = (
        "verified"
        if report_input.source_kind == "completed_reconciled_product"
        and report_input.reproduction_status == "verified"
        else "draft/unverified"
    )
    return EvidenceLinkedReport(report_input, claims, status)


def publish_report(report: EvidenceLinkedReport, output_root: Path | str) -> Path:
    """Write one content-addressed local report without replacing prior report bytes."""
    document = report.to_document()
    report_bytes = canonical_bytes(document)
    report_hash = sha256(report_bytes).hexdigest()
    markdown = _markdown(report)
    manifest = canonical_bytes(
        {
            "schema_version": "1.0.0",
            "artifact_type": "report_manifest",
            "report_id": report.report_input.report_id,
            "report_sha256": report_hash,
            "report_input_sha256": report.report_input.input_sha256,
            "renderer_version": REPORT_RENDERER_VERSION,
            "runtime_lock_sha256": report.report_input.runtime_lock_sha256,
            "template_sha256": report.report_input.template_sha256,
            "terminal_status": report.terminal_status,
        }
    )
    target = Path(output_root) / "reports" / report.report_input.report_id
    files = {
        "report.json": report_bytes,
        "report.md": markdown,
        "report-manifest.json": manifest,
    }
    if target.exists():
        if not target.is_dir() or any(
            not (target / name).is_file() or (target / name).read_bytes() != data
            for name, data in files.items()
        ):
            raise AnalysisError("report_identity_conflict")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.mkdir()
        for name, data in files.items():
            (temporary / name).write_bytes(data)
        temporary.replace(target)
    except OSError as error:
        if target.is_dir() and all(
            (target / name).is_file() and (target / name).read_bytes() == data
            for name, data in files.items()
        ):
            return target
        raise AnalysisError("report_publish_failed") from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def _markdown(report: EvidenceLinkedReport) -> bytes:
    scope = report.report_input.scope
    lines = [
        f"# Evidence-linked report: {report.report_input.report_id}",
        "",
        f"Stage: `{report.report_input.stage}`",
        f"Terminal status: `{report.terminal_status}`",
        "",
        "## Tested scope",
        "",
        f"Population: `{scope.population_id}`",
        f"Model: `{scope.model_id}`",
        f"Endpoint: `{scope.endpoint_id}`",
        f"Stratum: `{scope.stratum}`",
        f"History regime: `{scope.history_regime}`",
        f"Protocol: `{scope.protocol_sha256}`",
        "",
        "## Claims",
        "",
    ]
    lines.extend(f"- **{claim.terminal_status}**: {claim.language}" for claim in report.claims)
    lines.extend(("", "## Evidence sections", ""))
    lines.extend(f"- `{name}`" for name in _REQUIRED_SECTIONS)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
