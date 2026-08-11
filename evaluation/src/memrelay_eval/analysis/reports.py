"""Deterministic local projections of immutable evaluator evidence."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.errors import AnalysisError

from .claims import SOURCE_KINDS, BoundedClaim, ClaimScope, bound_claim
from .gates import (
    CategoricalGateDecision,
    CategoricalGatePolicy,
    ClaimGateDecision,
    ReleaseFitnessDecision,
    evaluate_release_fitness,
)
from .intervals import SimultaneousInterval
from .multiplicity import FrozenClaimFamily
from .queries import FrozenDataset

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
REPORT_TEMPLATE_SHA256 = sha256(b"evidence-linked-report-markdown-v2").hexdigest()


@dataclass(frozen=True, slots=True)
class StageScope:
    """Scope common to a stage; endpoint authority remains item-local."""

    protocol_sha256: str
    population_id: str
    model_id: str
    stratum: str
    history_regime: str
    environment_sha256: str
    source_sha256: tuple[str, ...]
    derivation_sha256: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all(
            (
                _is_sha256(self.protocol_sha256),
                _is_sha256(self.environment_sha256),
                _is_sha256(self.derivation_sha256),
                self.population_id,
                self.model_id,
                self.stratum,
                self.history_regime,
                self.source_sha256,
                self.evidence_ids,
            )
        ) or not all(_is_sha256(value) for value in self.source_sha256):
            raise AnalysisError("report_scope_invalid")

    def to_document(self) -> dict[str, object]:
        return {
            "protocol_sha256": self.protocol_sha256,
            "population_id": self.population_id,
            "model_id": self.model_id,
            "stratum": self.stratum,
            "history_regime": self.history_regime,
            "environment_sha256": self.environment_sha256,
            "source_sha256": list(self.source_sha256),
            "derivation_sha256": self.derivation_sha256,
            "evidence_ids": list(self.evidence_ids),
        }

    def contains(self, scope: ClaimScope) -> bool:
        return (
            scope.protocol_sha256 == self.protocol_sha256
            and scope.population_id == self.population_id
            and scope.model_id == self.model_id
            and scope.stratum == self.stratum
            and scope.history_regime == self.history_regime
            and scope.environment_sha256 == self.environment_sha256
            and scope.source_sha256 == self.source_sha256
            and scope.derivation_sha256 == self.derivation_sha256
            and scope.evidence_ids == self.evidence_ids
        )


@dataclass(frozen=True, slots=True)
class ReportItem:
    """One table, figure, or metric with its own endpoint scope."""

    item_id: str
    scope: ClaimScope
    value: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.item_id or not isinstance(self.value, Mapping):
            raise AnalysisError("report_item_invalid")

    def to_document(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "scope": self.scope.to_document(),
            "value": dict(self.value),
        }


@dataclass(frozen=True, slots=True)
class SourceAuthority:
    """Sealed provenance classification bound to the exact verified stage dataset."""

    source_kind: str
    dataset_manifest_sha256: str
    protocol_sha256: str
    source_sha256: tuple[str, ...]
    authority_sha256: str

    def __post_init__(self) -> None:
        if (
            self.source_kind not in SOURCE_KINDS
            or not _is_sha256(self.dataset_manifest_sha256)
            or not _is_sha256(self.protocol_sha256)
            or not self.source_sha256
            or not all(_is_sha256(value) for value in self.source_sha256)
            or self.authority_sha256 != canonical_digest(self._basis())
        ):
            raise AnalysisError("report_source_authority_invalid")

    def _basis(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "report_source_authority",
            "source_kind": self.source_kind,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "protocol_sha256": self.protocol_sha256,
            "source_sha256": list(self.source_sha256),
        }

    def to_document(self) -> dict[str, object]:
        return {**self._basis(), "authority_sha256": self.authority_sha256}


@dataclass(frozen=True, slots=True)
class ReportInput:
    """Inputs are typed analysis authority; release fitness is never caller-authored."""

    report_id: str
    stage: str
    scope: StageScope
    dataset_manifest_sha256: str
    table_sha256: tuple[str, ...]
    figure_sha256: tuple[str, ...]
    estimator_sha256: str
    interval_sha256: tuple[str, ...]
    power_sha256: str
    safety_sha256: str
    panel_sha256: str
    cost_revision_sha256: str
    runtime_lock_sha256: str
    template_sha256: str
    gate_ids: tuple[str, ...]
    family: FrozenClaimFamily
    claim_decisions: tuple[ClaimGateDecision, ...]
    claim_scopes: tuple[ClaimScope, ...]
    non_target_intervals: tuple[SimultaneousInterval, ...]
    categorical_policy: CategoricalGatePolicy
    categorical_decisions: tuple[CategoricalGateDecision, ...]
    source_authority: SourceAuthority
    reproduction_status: str
    sections: Mapping[str, tuple[ReportItem, ...]]

    def __post_init__(self) -> None:
        hashes = (
            self.dataset_manifest_sha256,
            self.estimator_sha256,
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
        if not self.report_id or not self.stage or not all(_is_sha256(value) for value in hashes):
            raise AnalysisError("report_input_lineage_invalid")
        if self.template_sha256 != REPORT_TEMPLATE_SHA256:
            raise AnalysisError("report_template_drift")
        if (
            not self.gate_ids
            or not self.claim_decisions
            or len(self.claim_decisions) != len(self.claim_scopes)
            or set(self.sections) != set(_REQUIRED_SECTIONS)
        ):
            raise AnalysisError("report_input_incomplete")
        if self.family.protocol_sha256 != self.scope.protocol_sha256:
            raise AnalysisError("report_family_scope_conflict")
        if (
            self.source_authority.dataset_manifest_sha256 != self.dataset_manifest_sha256
            or self.source_authority.protocol_sha256 != self.scope.protocol_sha256
            or self.source_authority.source_sha256 != self.scope.source_sha256
        ):
            raise AnalysisError("report_source_authority_conflict")
        if any(
            decision.family_sha256 != self.family.family_sha256
            or decision.protocol_sha256 != self.scope.protocol_sha256
            or decision.source_sha256 not in self.scope.source_sha256
            or decision.derivation_sha256 != self.scope.derivation_sha256
            or decision.endpoint_id != item_scope.endpoint_id
            or not self.scope.contains(item_scope)
            for decision, item_scope in zip(self.claim_decisions, self.claim_scopes, strict=True)
        ):
            raise AnalysisError("report_claim_lineage_conflict")
        if any(
            not values or any(not self.scope.contains(item.scope) for item in values)
            for values in self.sections.values()
        ):
            raise AnalysisError("report_item_lineage_conflict")

    @property
    def release_fitness(self) -> ReleaseFitnessDecision:
        """Recompute from the exact family, claims, intervals, and categorical authority."""
        decision = evaluate_release_fitness(
            target_decisions=self.claim_decisions,
            non_target_intervals=self.non_target_intervals,
            family=self.family,
            categorical_policy=self.categorical_policy,
            categorical_decisions=self.categorical_decisions,
            population_id=self.scope.population_id,
            model_id=self.scope.model_id,
            stratum=self.scope.stratum,
            history_regime=self.scope.history_regime,
            environment_sha256=self.scope.environment_sha256,
            source_sha256=self.scope.source_sha256,
            derivation_sha256=self.scope.derivation_sha256,
            evidence_sha256=tuple(
                sorted(
                    {
                        value
                        for decision in self.categorical_decisions
                        for value in decision.evidence_sha256
                    }
                )
            )
            or (self.safety_sha256,),
            reproduction_status=self.reproduction_status,
        )
        if self.source_authority.source_kind != "completed_reconciled_product":
            return replace(decision, status="draft/unverified")
        return decision

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
            "family": self.family.to_document(),
            "power_sha256": self.power_sha256,
            "safety_sha256": self.safety_sha256,
            "panel_sha256": self.panel_sha256,
            "cost_revision_sha256": self.cost_revision_sha256,
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "template_sha256": self.template_sha256,
            "gate_ids": list(self.gate_ids),
            "claim_decisions": [item.to_document() for item in self.claim_decisions],
            "claim_scopes": [item.to_document() for item in self.claim_scopes],
            "non_target_intervals": [
                _interval_document(item) for item in self.non_target_intervals
            ],
            "categorical_policy": self.categorical_policy.to_document(),
            "categorical_decisions": [item.to_document() for item in self.categorical_decisions],
            "source_authority": self.source_authority.to_document(),
            "reproduction_status": self.reproduction_status,
            "sections": {
                name: [item.to_document() for item in self.sections[name]]
                for name in _REQUIRED_SECTIONS
            },
            "release_fitness": self.release_fitness.to_document(),
        }


@dataclass(frozen=True, slots=True)
class EvidenceLinkedReport:
    report_input: ReportInput
    claims: tuple[BoundedClaim, ...]
    terminal_status: str

    def __post_init__(self) -> None:
        if self.terminal_status not in {"verified", "draft/unverified"} or not self.claims:
            raise AnalysisError("report_terminal_status_invalid")
        if self.terminal_status == "verified" and (
            self.report_input.release_fitness.status != "pass"
            or self.report_input.source_authority.source_kind != "completed_reconciled_product"
        ):
            raise AnalysisError("report_release_fitness_not_passed")

    @property
    def report_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "evidence_linked_report",
            "report_input_sha256": self.report_input.input_sha256,
            "renderer_version": REPORT_RENDERER_VERSION,
            "runtime_lock_sha256": self.report_input.runtime_lock_sha256,
            "template_sha256": self.report_input.template_sha256,
            "report_id": self.report_input.report_id,
            "stage": self.report_input.stage,
            "terminal_status": self.terminal_status,
            "scope": self.report_input.scope.to_document(),
            "source_authority": self.report_input.source_authority.to_document(),
            "input_hashes": {
                "dataset_manifest_sha256": self.report_input.dataset_manifest_sha256,
                "table_sha256": list(self.report_input.table_sha256),
                "figure_sha256": list(self.report_input.figure_sha256),
                "estimator_sha256": self.report_input.estimator_sha256,
                "interval_sha256": list(self.report_input.interval_sha256),
                "family_sha256": self.report_input.family.family_sha256,
                "power_sha256": self.report_input.power_sha256,
                "safety_sha256": self.report_input.safety_sha256,
                "panel_sha256": self.report_input.panel_sha256,
                "cost_revision_sha256": self.report_input.cost_revision_sha256,
            },
            "sections": {
                name: [item.to_document() for item in self.report_input.sections[name]]
                for name in _REQUIRED_SECTIONS
            },
            "claims": [item.to_document() for item in self.claims],
            "release_fitness": self.report_input.release_fitness.to_document(),
        }


def render_report(report_input: ReportInput) -> EvidenceLinkedReport:
    claims = tuple(
        bound_claim(
            decision,
            scope,
            source_kind=report_input.source_authority.source_kind,
            reproduction_status=report_input.reproduction_status,
        )
        for decision, scope in zip(
            report_input.claim_decisions, report_input.claim_scopes, strict=True
        )
    )
    status = "verified" if report_input.release_fitness.status == "pass" else "draft/unverified"
    return EvidenceLinkedReport(report_input, claims, status)


def build_stage_report_input(dataset: FrozenDataset, sealed_input: ReportInput) -> ReportInput:
    """Admit a sealed analysis projection only when it names this verified dataset."""
    if sealed_input.dataset_manifest_sha256 != dataset.manifest_sha256:
        raise AnalysisError("report_dataset_manifest_conflict")
    manifest = dataset.manifest
    if (
        sealed_input.scope.protocol_sha256 not in manifest["protocol_sha256"]
        or sealed_input.scope.population_id not in manifest["population_id"]
        or sealed_input.scope.stratum not in manifest["stratum"]
        or sealed_input.scope.history_regime not in manifest["history_mode"]
    ):
        raise AnalysisError("report_stage_scope_conflict")
    if sealed_input.source_authority.dataset_manifest_sha256 != dataset.manifest_sha256:
        raise AnalysisError("report_source_authority_conflict")
    source_manifest_sha256 = tuple(manifest["source_manifest_sha256"])
    if (
        sealed_input.scope.source_sha256 != source_manifest_sha256
        or sealed_input.source_authority.source_sha256 != source_manifest_sha256
    ):
        raise AnalysisError("report_source_authority_conflict")
    return sealed_input


def publish_report(report: EvidenceLinkedReport, output_root: Path | str) -> Path:
    document = report.to_document()
    report_bytes = canonical_bytes(document)
    target = Path(output_root) / "reports" / report.report_input.report_id
    files = {
        "report.json": report_bytes,
        "frozen-report-input.json": canonical_bytes(report.report_input.to_document()),
        "report-manifest.json": canonical_bytes(
            {
                "schema_version": "1.0.0",
                "artifact_type": "report_manifest",
                "report_id": report.report_input.report_id,
                "report_sha256": sha256(report_bytes).hexdigest(),
                "report_input_sha256": report.report_input.input_sha256,
                "renderer_version": REPORT_RENDERER_VERSION,
                "runtime_lock_sha256": report.report_input.runtime_lock_sha256,
                "template_sha256": report.report_input.template_sha256,
                "terminal_status": report.terminal_status,
                "source_authority_sha256": report.report_input.source_authority.authority_sha256,
            }
        ),
    }
    if target.exists():
        if not target.is_dir() or any(
            (target / name).read_bytes() != data for name, data in files.items()
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
        raise AnalysisError("report_publish_failed") from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def _interval_document(interval: SimultaneousInterval) -> dict[str, object]:
    return {
        "endpoint_id": interval.endpoint_id,
        "point_estimate": interval.point_estimate,
        "lower": interval.lower,
        "upper": interval.upper,
        "confidence_level": interval.confidence_level,
        "procedure": interval.procedure,
        "family_sha256": interval.family_sha256,
        "status": interval.status,
        "sidedness": interval.sidedness,
    }


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
