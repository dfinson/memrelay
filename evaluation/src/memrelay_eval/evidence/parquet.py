"""Deterministic, immutable Parquet materialization from Story 4.5 decisions only."""

from __future__ import annotations

import errno
import json
import math
import os
import shutil
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from memrelay_eval.analysis.schemas import (
    ASSIGNED_UNITS_TABLE,
    ELIGIBLE_OUTCOMES_TABLE,
    PARQUET_SCHEMA_VERSION,
    assigned_units_schema,
    categories_for,
    eligible_outcomes_schema,
    require_pyarrow_25,
    schema_sha256,
)
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.entities import ArtifactManifest, ArtifactRef, InclusionDecision
from memrelay_eval.domain.errors import ArtifactIntegrityError, MaterializationError
from memrelay_eval.domain.ids import ArtifactId, ExperimentId
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.domain.states import ArtifactScope, InclusionStatus
from memrelay_eval.evidence.manifest import parse_manifest
from memrelay_eval.evidence.projection import EvidencePresence
from memrelay_eval.evidence.reconcile import (
    ReconciliationInput,
    ReconciliationReport,
    inclusion_decision_document,
)

MATERIALIZATION_SCHEMA_VERSION = "1.0.0"
OUTCOME_AUTHORITY_SCHEMA_VERSION = "1.0.0"
_PARQUET_SUFFIX = ".parquet"
_SHA256_LENGTH = 64


@dataclass(frozen=True, slots=True)
class CostObservation:
    """A preserved quantity keeps observed zero separate from null and unavailable."""

    logical_ledger: str
    unit: str
    quantity: Decimal | int | None
    availability: str

    def __post_init__(self) -> None:
        if not self.logical_ledger or not self.unit:
            raise MaterializationError("cost_identity_missing")
        if self.availability not in categories_for("availability"):
            raise MaterializationError("cost_availability_invalid")
        if self.availability == "observed":
            if self.quantity is None:
                raise MaterializationError("observed_cost_quantity_missing")
            _decimal_quantity(self.quantity)
        elif self.quantity is not None:
            raise MaterializationError("unavailable_cost_quantity_must_be_null")

    def to_document(self) -> dict[str, object]:
        return {
            "availability": self.availability,
            "logical_ledger": self.logical_ledger,
            "quantity": None if self.quantity is None else str(_decimal_quantity(self.quantity)),
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class AssignedUnit:
    """The complete assigned-unit denominator retained across inclusion outcomes."""

    analysis_unit_id: str
    population_id: str
    attrition_status: str
    exposure_status: str
    contamination_status: str
    failure_reason: str | None
    costs: tuple[CostObservation, ...] = ()

    def __post_init__(self) -> None:
        if not self.analysis_unit_id or not self.population_id:
            raise MaterializationError("assigned_unit_identity_missing")
        if self.attrition_status not in categories_for("attrition_status"):
            raise MaterializationError("attrition_status_invalid")
        if self.exposure_status not in categories_for("exposure_status"):
            raise MaterializationError("exposure_status_invalid")
        if self.contamination_status not in categories_for("contamination_status"):
            raise MaterializationError("contamination_status_invalid")
        if self.failure_reason is not None and not self.failure_reason:
            raise MaterializationError("failure_reason_invalid")
        object.__setattr__(self, "costs", tuple(self.costs))

    def to_document(self) -> dict[str, object]:
        return {
            "analysis_unit_id": self.analysis_unit_id,
            "attrition_status": self.attrition_status,
            "contamination_status": self.contamination_status,
            "costs": [item.to_document() for item in self.costs],
            "exposure_status": self.exposure_status,
            "failure_reason": self.failure_reason,
            "population_id": self.population_id,
        }


@dataclass(frozen=True, slots=True)
class EligibleOutcome:
    """One included, observed endpoint measurement authorized for confirmatory use."""

    endpoint_id: str
    outcome_id: str
    outcome_kind: str
    unit: str
    evidence_refs: tuple[ArtifactRef, ...]
    numeric_value: float | None = None
    category_value: str | None = None
    authority_ref: ArtifactRef | None = None
    authority_manifest_ref: ArtifactRef | None = None

    def __post_init__(self) -> None:
        if not self.endpoint_id or not self.outcome_id or not self.unit:
            raise MaterializationError("eligible_outcome_identity_missing")
        if self.outcome_kind not in categories_for("outcome_kind"):
            raise MaterializationError("eligible_outcome_kind_invalid")
        if self.outcome_kind == "numeric":
            if self.numeric_value is None or self.category_value is not None:
                raise MaterializationError("numeric_outcome_shape_invalid")
            if not math.isfinite(self.numeric_value):
                raise MaterializationError("numeric_outcome_not_finite")
        elif (
            self.category_value is None or not self.category_value or self.numeric_value is not None
        ):
            raise MaterializationError("categorical_outcome_shape_invalid")
        if not self.evidence_refs or any(
            not isinstance(value, ArtifactRef) for value in self.evidence_refs
        ):
            raise MaterializationError("eligible_outcome_evidence_invalid")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise MaterializationError("eligible_outcome_evidence_duplicate")
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(sorted(self.evidence_refs, key=lambda item: item.sha256)),
        )

    def to_document(self) -> dict[str, object]:
        return {
            "category_value": self.category_value,
            "endpoint_id": self.endpoint_id,
            "evidence_sha256": [item.sha256 for item in self.evidence_refs],
            "numeric_value": None if self.numeric_value is None else repr(self.numeric_value),
            "outcome_id": self.outcome_id,
            "outcome_kind": self.outcome_kind,
            "unit": self.unit,
        }


def seal_eligible_outcome_authority(
    artifact_store: ArtifactStorePort,
    outcome: EligibleOutcome,
    request: ReconciliationInput,
) -> EligibleOutcome:
    """Persist a value-binding endpoint authority before Parquet consumes it."""
    if outcome.authority_ref is not None or outcome.authority_manifest_ref is not None:
        raise MaterializationError("eligible_outcome_authority_already_sealed")
    payload = canonical_bytes(_eligible_outcome_authority_document(outcome))
    ref = artifact_store.put_bytes(
        payload,
        media_type="application/json",
        classification="eligible_outcome_authority",
    )
    manifest = ArtifactManifest(
        artifact_id=ref.artifact_id,
        kind="eligible_outcome_authority",
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        media_type="application/json",
        created_at=request.reconciled_at,
        producer_component="scoring_outcomes",
        producer_version=OUTCOME_AUTHORITY_SCHEMA_VERSION,
        classification="eligible_outcome_authority",
        contains_secrets=False,
        source_artifact_ids=tuple(item.artifact_id for item in outcome.evidence_refs),
        retention_policy_id=request.retention_policy_id,
        encryption=None,
        scope=ArtifactScope.ATTEMPT,
        run_id=request.run_id,
        attempt_id=request.attempt_id,
    )
    try:
        manifest_ref = artifact_store.write_manifest(manifest)
    except ArtifactIntegrityError as error:
        raise MaterializationError("eligible_outcome_authority_manifest_unavailable") from error
    return EligibleOutcome(
        endpoint_id=outcome.endpoint_id,
        outcome_id=outcome.outcome_id,
        outcome_kind=outcome.outcome_kind,
        unit=outcome.unit,
        evidence_refs=outcome.evidence_refs,
        numeric_value=outcome.numeric_value,
        category_value=outcome.category_value,
        authority_ref=ref,
        authority_manifest_ref=manifest_ref,
    )


@dataclass(frozen=True, slots=True)
class ReconciledTerminalRecord:
    """An immutable Story 4.5 decision bundle, consumed without ledger access."""

    request: ReconciliationInput
    report: ReconciliationReport
    report_ref: ArtifactRef
    report_manifest_ref: ArtifactRef
    decision: InclusionDecision
    decision_ref: ArtifactRef
    decision_manifest_ref: ArtifactRef
    assigned_unit: AssignedUnit
    eligible_outcomes: tuple[EligibleOutcome, ...] = ()

    def __post_init__(self) -> None:
        if self.decision.run_id != self.request.run_id:
            raise MaterializationError("decision_run_identity_conflict")
        if self.decision.reconciliation_sha256 != self.report.reconciliation_sha256:
            raise MaterializationError("decision_reconciliation_conflict")
        if self.report.input.to_document() != self.request.to_document():
            raise MaterializationError("reconciliation_report_input_conflict")
        if self.report.inclusion_status is not self.decision.status:
            raise MaterializationError("decision_status_conflict")
        outcomes = tuple(self.eligible_outcomes)
        if self.decision.status is InclusionStatus.EXCLUDED and outcomes:
            raise MaterializationError("excluded_measurement_cannot_be_eligible")
        outcome_ids = [(item.endpoint_id, item.outcome_id) for item in outcomes]
        if len(set(outcome_ids)) != len(outcome_ids):
            raise MaterializationError("duplicate_eligible_outcome_identity")
        object.__setattr__(
            self,
            "eligible_outcomes",
            tuple(sorted(outcomes, key=lambda item: (item.endpoint_id, item.outcome_id))),
        )

    def input_document(self) -> dict[str, object]:
        return {
            "assigned_unit": self.assigned_unit.to_document(),
            "decision": {
                "reason": self.decision.reason,
                "reconciliation_sha256": self.decision.reconciliation_sha256,
                "status": self.decision.status.value,
            },
            "eligible_outcomes": [item.to_document() for item in self.eligible_outcomes],
            "report_manifest_sha256": self.report_manifest_ref.sha256,
            "report_sha256": self.report_ref.sha256,
            "decision_manifest_sha256": self.decision_manifest_ref.sha256,
            "decision_sha256": self.decision_ref.sha256,
            "request": self.request.to_document(),
        }


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    """Published immutable dataset references and the local version directory."""

    dataset_version: str
    directory: Path
    materialization_sha256: str
    assigned_units_ref: ArtifactRef
    eligible_outcomes_ref: ArtifactRef
    dataset_manifest_ref: ArtifactRef
    derivation_manifest_ref: ArtifactRef


class ReconciledInputResolver:
    """Verify immutable Story 4.5 evidence and form the complete analysis denominator."""

    def __init__(self, artifact_store: ArtifactStorePort) -> None:
        self._artifact_store = artifact_store

    def resolve(
        self, records: Iterable[ReconciledTerminalRecord]
    ) -> tuple[ReconciledTerminalRecord, ...]:
        resolved = tuple(records)
        if not resolved:
            raise MaterializationError("reconciled_terminal_records_missing")
        identity_keys: set[tuple[str, str]] = set()
        assignment_keys: set[str] = set()
        for record in resolved:
            self._verify_record_lineage(record)
            identity = (str(record.request.run_id), str(record.request.attempt_id))
            if identity in identity_keys:
                raise MaterializationError("duplicate_terminal_identity")
            identity_keys.add(identity)
            assignment_id = record.request.identities["assignment_id"]
            if assignment_id in assignment_keys:
                raise MaterializationError("duplicate_assignment_identity")
            assignment_keys.add(assignment_id)
        return tuple(sorted(resolved, key=_assigned_sort_key))

    def _verify_record_lineage(self, record: ReconciledTerminalRecord) -> None:
        report_bytes = self._artifact_store.open_verified(record.report_ref)
        if report_bytes != canonical_bytes(record.report.to_document()):
            raise MaterializationError("reconciliation_report_tampered")
        if record.report.reconciliation_sha256 != canonical_digest(record.report.basis_document()):
            raise MaterializationError("reconciliation_report_digest_conflict")
        report_manifest_data = self._artifact_store.open_verified(record.report_manifest_ref)
        report_manifest = parse_manifest(report_manifest_data)
        authoritative_report_manifest = self._artifact_store.read_manifest(record.report_ref)
        if report_manifest.to_dict() != authoritative_report_manifest.to_dict():
            raise MaterializationError("reconciliation_report_manifest_conflict")
        if report_manifest.sha256 != record.report_ref.sha256:
            raise MaterializationError("reconciliation_report_manifest_binding_conflict")
        if report_manifest.kind != "reconciliation_report":
            raise MaterializationError("reconciliation_report_manifest_kind_invalid")
        if (
            report_manifest.producer_component != "evidence_reconcile"
            or report_manifest.producer_version != record.report.input.schema_version
        ):
            raise MaterializationError("reconciliation_report_producer_conflict")
        if (
            report_manifest.run_id != record.request.run_id
            or report_manifest.attempt_id != record.request.attempt_id
        ):
            raise MaterializationError("reconciliation_report_manifest_identity_conflict")
        self._verify_decision_authority(record)
        report_sources = {item.sha256 for item in record.report.verified_sources}
        input_sources: set[str] = set()
        authorized_outcome_sources = {record.report_ref.sha256}
        for evidence in record.request.evidence:
            if evidence.artifact is None or evidence.manifest is None:
                if evidence.status is EvidencePresence.PRESENT:
                    raise MaterializationError("reconciliation_evidence_lineage_missing")
                continue
            input_sources.add(evidence.artifact.sha256)
            authorized_outcome_sources.add(evidence.artifact.sha256)
            self._artifact_store.open_verified(evidence.artifact)
            evidence_manifest_bytes = self._artifact_store.open_verified(evidence.manifest)
            evidence_manifest = parse_manifest(evidence_manifest_bytes)
            authoritative_evidence_manifest = self._artifact_store.read_manifest(evidence.artifact)
            if evidence_manifest.to_dict() != authoritative_evidence_manifest.to_dict():
                raise MaterializationError("source_manifest_conflict")
            if (
                evidence_manifest.sha256 != evidence.artifact.sha256
                or evidence_manifest.run_id != record.request.run_id
                or evidence_manifest.attempt_id != record.request.attempt_id
            ):
                raise MaterializationError("source_manifest_binding_conflict")
        if report_sources != input_sources:
            raise MaterializationError("reconciliation_source_lineage_conflict")
        for outcome in record.eligible_outcomes:
            self._verify_outcome_authority(
                outcome,
                record.request,
                authorized_outcome_sources,
            )

    def _verify_outcome_authority(
        self,
        outcome: EligibleOutcome,
        request: ReconciliationInput,
        authorized_sources: set[str],
    ) -> None:
        if outcome.authority_ref is None or outcome.authority_manifest_ref is None:
            raise MaterializationError("eligible_outcome_authority_missing")
        authority_bytes = self._artifact_store.open_verified(outcome.authority_ref)
        if authority_bytes != canonical_bytes(_eligible_outcome_authority_document(outcome)):
            raise MaterializationError("eligible_outcome_authority_conflict")
        manifest_bytes = self._artifact_store.open_verified(outcome.authority_manifest_ref)
        manifest = parse_manifest(manifest_bytes)
        authoritative_manifest = self._artifact_store.read_manifest(outcome.authority_ref)
        if manifest.to_dict() != authoritative_manifest.to_dict():
            raise MaterializationError("eligible_outcome_authority_manifest_conflict")
        if (
            manifest.kind != "eligible_outcome_authority"
            or manifest.producer_component != "scoring_outcomes"
            or manifest.producer_version != OUTCOME_AUTHORITY_SCHEMA_VERSION
            or manifest.run_id != request.run_id
            or manifest.attempt_id != request.attempt_id
            or manifest.source_artifact_ids
            != tuple(item.artifact_id for item in outcome.evidence_refs)
        ):
            raise MaterializationError("eligible_outcome_authority_manifest_binding_conflict")
        for evidence_ref in outcome.evidence_refs:
            if evidence_ref.sha256 not in authorized_sources:
                raise MaterializationError("eligible_outcome_evidence_not_authorized")
            self._artifact_store.open_verified(evidence_ref)

    def _verify_decision_authority(self, record: ReconciledTerminalRecord) -> None:
        decision_bytes = self._artifact_store.open_verified(record.decision_ref)
        expected = canonical_bytes(inclusion_decision_document(record.decision, record.report_ref))
        if decision_bytes != expected:
            raise MaterializationError("inclusion_decision_authority_conflict")
        manifest_bytes = self._artifact_store.open_verified(record.decision_manifest_ref)
        manifest = parse_manifest(manifest_bytes)
        authoritative_manifest = self._artifact_store.read_manifest(record.decision_ref)
        if manifest.to_dict() != authoritative_manifest.to_dict():
            raise MaterializationError("inclusion_decision_manifest_conflict")
        if (
            manifest.kind != "inclusion_decision"
            or manifest.producer_component != "evidence_reconcile"
            or manifest.producer_version != record.report.input.schema_version
            or manifest.run_id != record.request.run_id
            or manifest.attempt_id != record.request.attempt_id
            or manifest.source_artifact_ids != (record.report_ref.artifact_id,)
        ):
            raise MaterializationError("inclusion_decision_manifest_binding_conflict")


class ParquetMaterializer:
    """Write verified, canonical table bytes into an immutable dataset version."""

    def __init__(self, artifact_store: ArtifactStorePort, output_root: Path | str) -> None:
        self._artifact_store = artifact_store
        self._resolver = ReconciledInputResolver(artifact_store)
        self._output_root = Path(output_root)

    def materialize(self, records: Iterable[ReconciledTerminalRecord]) -> MaterializationResult:
        require_pyarrow_25()
        resolved = self._resolver.resolve(records)
        assigned_schema = assigned_units_schema()
        outcomes_schema = eligible_outcomes_schema()
        input_digest = canonical_digest(
            {
                "records": [item.input_document() for item in resolved],
                "schema_version": MATERIALIZATION_SCHEMA_VERSION,
                "schemas": {
                    ASSIGNED_UNITS_TABLE: schema_sha256(assigned_schema),
                    ELIGIBLE_OUTCOMES_TABLE: schema_sha256(outcomes_schema),
                },
            }
        )
        dataset_version = f"parquet-v{PARQUET_SCHEMA_VERSION}-{input_digest[:16]}"
        assigned_rows = [_assigned_row(item, dataset_version) for item in resolved]
        outcome_rows = [
            _outcome_row(record, outcome, dataset_version)
            for record in resolved
            for outcome in record.eligible_outcomes
        ]
        assigned_table = _table_from_rows(assigned_rows, assigned_schema)
        outcomes_table = _table_from_rows(outcome_rows, outcomes_schema)
        materialization_sha256 = canonical_digest(
            {
                "dataset_version": dataset_version,
                "input_sha256": input_digest,
                "assigned_units_schema_sha256": schema_sha256(assigned_schema),
                "eligible_outcomes_schema_sha256": schema_sha256(outcomes_schema),
                "pyarrow_version": pa.__version__,
            }
        )
        result = self._publish(
            dataset_version,
            materialization_sha256,
            resolved,
            assigned_table,
            outcomes_table,
        )
        self._verify_published(result)
        return result

    def _publish(
        self,
        dataset_version: str,
        materialization_sha256: str,
        records: Sequence[ReconciledTerminalRecord],
        assigned_table: pa.Table,
        outcomes_table: pa.Table,
    ) -> MaterializationResult:
        self._output_root.mkdir(parents=True, exist_ok=True)
        destination = self._output_root / dataset_version
        if destination.exists():
            return self._load_existing(destination, dataset_version, materialization_sha256)
        staging = self._output_root / f".{dataset_version}.{uuid.uuid4().hex}.staging"
        try:
            staging.mkdir()
            assigned_path = staging / f"{ASSIGNED_UNITS_TABLE}{_PARQUET_SUFFIX}"
            outcomes_path = staging / f"{ELIGIBLE_OUTCOMES_TABLE}{_PARQUET_SUFFIX}"
            _write_table(assigned_table, assigned_path)
            _write_table(outcomes_table, outcomes_path)
            table_sources = tuple(record.decision_ref for record in records) + tuple(
                outcome.authority_ref
                for record in records
                for outcome in record.eligible_outcomes
                if outcome.authority_ref is not None
            )
            assigned_ref = self._persist_table(
                assigned_path.read_bytes(),
                records,
                "parquet_assigned_units",
                table_sources,
            )
            outcomes_ref = self._persist_table(
                outcomes_path.read_bytes(),
                records,
                "parquet_eligible_outcomes",
                table_sources,
            )
            manifest_document = _dataset_manifest_document(
                dataset_version,
                materialization_sha256,
                records,
                assigned_table.schema,
                outcomes_table.schema,
                assigned_ref,
                outcomes_ref,
            )
            manifest_ref = self._persist_manifest_document(
                canonical_bytes(manifest_document),
                records,
                "parquet_dataset_manifest",
                (assigned_ref, outcomes_ref, *table_sources),
            )
            derivation_document = _derivation_manifest_document(
                manifest_document, manifest_ref, assigned_ref, outcomes_ref
            )
            derivation_ref = self._persist_manifest_document(
                canonical_bytes(derivation_document),
                records,
                "parquet_derivation_manifest",
                (manifest_ref, assigned_ref, outcomes_ref),
            )
            _write_bytes(staging / "dataset-manifest.json", canonical_bytes(manifest_document))
            _write_bytes(staging / "derivation-manifest.json", canonical_bytes(derivation_document))
            _fsync_directory(staging)
            try:
                os.replace(staging, destination)
            except OSError as error:
                if _destination_exists_collision(error, destination):
                    return self._load_existing(destination, dataset_version, materialization_sha256)
                raise
            _fsync_directory(self._output_root)
            return MaterializationResult(
                dataset_version,
                destination,
                materialization_sha256,
                assigned_ref,
                outcomes_ref,
                manifest_ref,
                derivation_ref,
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _persist_table(
        self,
        data: bytes,
        records: Sequence[ReconciledTerminalRecord],
        kind: str,
        source_refs: Sequence[ArtifactRef],
    ) -> ArtifactRef:
        ref = self._artifact_store.put_bytes(
            data, media_type="application/vnd.apache.parquet", classification=kind
        )
        self._write_derived_manifest(
            ref, records, kind, "application/vnd.apache.parquet", source_refs
        )
        return ref

    def _persist_manifest_document(
        self,
        data: bytes,
        records: Sequence[ReconciledTerminalRecord],
        kind: str,
        source_refs: Sequence[ArtifactRef],
    ) -> ArtifactRef:
        ref = self._artifact_store.put_bytes(
            data, media_type="application/json", classification=kind
        )
        self._write_derived_manifest(ref, records, kind, "application/json", source_refs)
        return ref

    def _write_derived_manifest(
        self,
        ref: ArtifactRef,
        records: Sequence[ReconciledTerminalRecord],
        kind: str,
        media_type: str,
        source_refs: Sequence[ArtifactRef],
    ) -> None:
        first = records[0]
        sources = tuple(
            sorted(
                {reference.artifact_id for reference in source_refs},
                key=str,
            )
        )
        manifest = ArtifactManifest(
            artifact_id=ref.artifact_id,
            kind=kind,
            sha256=ref.sha256,
            size_bytes=ref.size_bytes,
            media_type=media_type,
            created_at=max(record.request.reconciled_at for record in records),
            producer_component="evidence_parquet",
            producer_version=MATERIALIZATION_SCHEMA_VERSION,
            classification=kind,
            contains_secrets=False,
            source_artifact_ids=sources,
            retention_policy_id=first.request.retention_policy_id,
            encryption=None,
            scope=ArtifactScope.EXPERIMENT,
            experiment_id=ExperimentId(first.request.identities["experiment_id"]),
        )
        try:
            self._artifact_store.write_manifest(manifest)
        except ArtifactIntegrityError as error:
            raise MaterializationError("materialization_manifest_unavailable") from error

    def _load_existing(
        self, destination: Path, dataset_version: str, materialization_sha256: str
    ) -> MaterializationResult:
        document = _load_canonical_json(destination / "dataset-manifest.json")
        if (
            document.get("dataset_version") != dataset_version
            or document.get("materialization_sha256") != materialization_sha256
        ):
            raise MaterializationError("published_dataset_version_conflict")
        derivation = _load_canonical_json(destination / "derivation-manifest.json")
        required = (
            "assigned_units_sha256",
            "eligible_outcomes_sha256",
            "dataset_manifest_sha256",
            "derivation_sha256",
        )
        if not all(
            isinstance(derivation.get(key), str) and _is_sha256(derivation[key]) for key in required
        ):
            raise MaterializationError("published_derivation_manifest_invalid")
        derivation_basis = dict(derivation)
        derivation_hash = derivation_basis.pop("derivation_sha256")
        if derivation_hash != canonical_digest(derivation_basis):
            raise MaterializationError("published_derivation_manifest_tampered")
        paths = {
            ASSIGNED_UNITS_TABLE: destination / f"{ASSIGNED_UNITS_TABLE}{_PARQUET_SUFFIX}",
            ELIGIBLE_OUTCOMES_TABLE: destination / f"{ELIGIBLE_OUTCOMES_TABLE}{_PARQUET_SUFFIX}",
        }
        if not all(path.is_file() for path in paths.values()):
            raise MaterializationError("published_dataset_partial")
        assigned_ref = _reference_from_document(document, ASSIGNED_UNITS_TABLE)
        outcomes_ref = _reference_from_document(document, ELIGIBLE_OUTCOMES_TABLE)
        if ArtifactRef.from_bytes(paths[ASSIGNED_UNITS_TABLE].read_bytes()) != assigned_ref:
            raise MaterializationError("published_assigned_units_tampered")
        if ArtifactRef.from_bytes(paths[ELIGIBLE_OUTCOMES_TABLE].read_bytes()) != outcomes_ref:
            raise MaterializationError("published_eligible_outcomes_tampered")
        manifest_ref = ArtifactRef.from_bytes(canonical_bytes(document))
        derivation_ref = ArtifactRef.from_bytes(canonical_bytes(derivation))
        if derivation["dataset_manifest_sha256"] != manifest_ref.sha256:
            raise MaterializationError("published_dataset_manifest_binding_conflict")
        self._artifact_store.open_verified(manifest_ref)
        self._artifact_store.open_verified(derivation_ref)
        return MaterializationResult(
            dataset_version,
            destination,
            materialization_sha256,
            assigned_ref,
            outcomes_ref,
            manifest_ref,
            derivation_ref,
        )

    def _verify_published(self, result: MaterializationResult) -> None:
        assigned_path = result.directory / f"{ASSIGNED_UNITS_TABLE}{_PARQUET_SUFFIX}"
        outcomes_path = result.directory / f"{ELIGIBLE_OUTCOMES_TABLE}{_PARQUET_SUFFIX}"
        assigned_one = pq.read_table(assigned_path)
        assigned_two = pq.ParquetFile(assigned_path).read()
        outcomes_one = pq.read_table(outcomes_path)
        outcomes_two = pq.ParquetFile(outcomes_path).read()
        _require_reader_agreement(assigned_one, assigned_two)
        _require_reader_agreement(outcomes_one, outcomes_two)
        if ArtifactRef.from_bytes(assigned_path.read_bytes()) != result.assigned_units_ref:
            raise MaterializationError("published_assigned_units_tampered")
        if ArtifactRef.from_bytes(outcomes_path.read_bytes()) != result.eligible_outcomes_ref:
            raise MaterializationError("published_eligible_outcomes_tampered")


def _assigned_sort_key(record: ReconciledTerminalRecord) -> tuple[str, ...]:
    identities = record.request.identities
    return (
        identities["experiment_id"],
        record.request.matrix_key.stratum.value,
        record.request.matrix_key.history_mode.value,
        identities["sequence_id"],
        identities["assignment_id"],
        identities["run_id"],
        identities["attempt_id"],
    )


def _assigned_row(record: ReconciledTerminalRecord, dataset_version: str) -> dict[str, object]:
    request = record.request
    identities = request.identities
    source_manifest_hashes = tuple(
        sorted(
            evidence.manifest.sha256
            for evidence in request.evidence
            if evidence.manifest is not None
        )
    )
    return {
        "schema_version": PARQUET_SCHEMA_VERSION,
        "dataset_version": dataset_version,
        "experiment_id": identities["experiment_id"],
        "protocol_sha256": request.protocol_sha256,
        "population_id": record.assigned_unit.population_id,
        "assignment_id": identities["assignment_id"],
        "analysis_unit_id": record.assigned_unit.analysis_unit_id,
        "run_id": identities["run_id"],
        "attempt_id": identities["attempt_id"],
        "task_id": identities["task_id"],
        "replicate_id": identities["replicate_id"],
        "history_id": identities["history_id"],
        "sequence_id": identities["sequence_id"],
        "repository_id": identities["repository_id"],
        "model_id": identities["model_id"],
        "environment_fingerprint_sha256": request.frozen_hashes["environment_fingerprint"],
        "stratum": request.matrix_key.stratum.value,
        "history_mode": request.matrix_key.history_mode.value,
        "terminal_kind": request.matrix_key.failure_state.value,
        "inclusion_status": record.decision.status.value,
        "inclusion_reason": record.decision.reason,
        "reconciliation_sha256": record.decision.reconciliation_sha256,
        "attrition_status": record.assigned_unit.attrition_status,
        "exposure_status": record.assigned_unit.exposure_status,
        "contamination_status": record.assigned_unit.contamination_status,
        "failure_reason": record.assigned_unit.failure_reason,
        "outcome_measurement_status": (
            "eligible" if record.decision.status is InclusionStatus.INCLUDED else "excluded"
        ),
        "started_at": request.started_at,
        "terminal_at": request.terminal_at,
        "reconciled_at": request.reconciled_at,
        "costs": [
            {
                "logical_ledger": item.logical_ledger,
                "unit": item.unit,
                "quantity": None if item.quantity is None else _decimal_quantity(item.quantity),
                "availability": item.availability,
            }
            for item in record.assigned_unit.costs
        ],
        "evidence": [
            {
                "artifact_id": str(evidence.artifact.artifact_id),
                "sha256": evidence.artifact.sha256,
                "size_bytes": evidence.artifact.size_bytes,
                "kind": evidence.kind.value,
                "manifest_sha256": evidence.manifest.sha256,
            }
            for evidence in sorted(request.evidence, key=lambda item: item.kind.value)
            if evidence.artifact is not None and evidence.manifest is not None
        ],
        "source_manifest_sha256": list(source_manifest_hashes),
    }


def _outcome_row(
    record: ReconciledTerminalRecord, outcome: EligibleOutcome, dataset_version: str
) -> dict[str, object]:
    request = record.request
    identities = request.identities
    return {
        "schema_version": PARQUET_SCHEMA_VERSION,
        "dataset_version": dataset_version,
        "experiment_id": identities["experiment_id"],
        "protocol_sha256": request.protocol_sha256,
        "population_id": record.assigned_unit.population_id,
        "assignment_id": identities["assignment_id"],
        "analysis_unit_id": record.assigned_unit.analysis_unit_id,
        "run_id": identities["run_id"],
        "attempt_id": identities["attempt_id"],
        "task_id": identities["task_id"],
        "replicate_id": identities["replicate_id"],
        "history_id": identities["history_id"],
        "sequence_id": identities["sequence_id"],
        "repository_id": identities["repository_id"],
        "model_id": identities["model_id"],
        "environment_fingerprint_sha256": request.frozen_hashes["environment_fingerprint"],
        "stratum": request.matrix_key.stratum.value,
        "history_mode": request.matrix_key.history_mode.value,
        "endpoint_id": outcome.endpoint_id,
        "outcome_id": outcome.outcome_id,
        "outcome_kind": outcome.outcome_kind,
        "numeric_value": outcome.numeric_value,
        "category_value": outcome.category_value,
        "unit": outcome.unit,
        "evidence_sha256": [item.sha256 for item in outcome.evidence_refs],
        "reconciliation_sha256": record.decision.reconciliation_sha256,
    }


def _eligible_outcome_authority_document(outcome: EligibleOutcome) -> dict[str, object]:
    """Canonical source-to-value binding for one confirmatory endpoint observation."""
    return {
        "schema_version": OUTCOME_AUTHORITY_SCHEMA_VERSION,
        "artifact_type": "eligible_outcome_authority",
        "endpoint_id": outcome.endpoint_id,
        "outcome_id": outcome.outcome_id,
        "outcome_kind": outcome.outcome_kind,
        "numeric_value": outcome.numeric_value,
        "category_value": outcome.category_value,
        "unit": outcome.unit,
        "evidence": [_ref_document(item) for item in outcome.evidence_refs],
    }


def _table_from_rows(rows: Sequence[Mapping[str, object]], schema: pa.Schema) -> pa.Table:
    columns: list[pa.Array] = []
    for field in schema:
        values = [row.get(field.name) for row in rows]
        if pa.types.is_dictionary(field.type):
            columns.append(_dictionary_array(values, field.name))
        else:
            columns.append(pa.array(values, type=field.type))
    return pa.Table.from_arrays(columns, schema=schema)


def _dictionary_array(values: Sequence[object], field_name: str) -> pa.DictionaryArray:
    categories = categories_for(field_name)
    positions = {value: index for index, value in enumerate(categories)}
    indices: list[int | None] = []
    for value in values:
        if value is None:
            indices.append(None)
        elif not isinstance(value, str) or value not in positions:
            raise MaterializationError("categorical_value_invalid")
        else:
            indices.append(positions[value])
    return pa.DictionaryArray.from_arrays(
        pa.array(indices, type=pa.int8()),
        pa.array(categories, type=pa.string()),
    )


def _write_table(table: pa.Table, path: Path) -> None:
    pq.write_table(
        table,
        path,
        compression="zstd",
        compression_level=3,
        use_dictionary=True,
        write_statistics=True,
        row_group_size=1024,
        data_page_version="1.0",
        coerce_timestamps="us",
        allow_truncated_timestamps=False,
    )
    _fsync_file(path)


def _dataset_manifest_document(
    dataset_version: str,
    materialization_sha256: str,
    records: Sequence[ReconciledTerminalRecord],
    assigned_schema: pa.Schema,
    outcomes_schema: pa.Schema,
    assigned_ref: ArtifactRef,
    outcomes_ref: ArtifactRef,
) -> dict[str, object]:
    source_manifest_sha256 = sorted(
        {
            evidence.manifest.sha256
            for record in records
            for evidence in record.request.evidence
            if evidence.manifest is not None
        }
    )
    return {
        "schema_version": MATERIALIZATION_SCHEMA_VERSION,
        "artifact_type": "parquet_dataset_manifest",
        "dataset_version": dataset_version,
        "materialization_sha256": materialization_sha256,
        "pyarrow_version": pa.__version__,
        "source_manifest_sha256": source_manifest_sha256,
        "reconciliation_sha256": sorted(
            {record.decision.reconciliation_sha256 for record in records}
        ),
        "reconciliation_report_sha256": sorted({record.report_ref.sha256 for record in records}),
        "reconciliation_report_manifest_sha256": sorted(
            {record.report_manifest_ref.sha256 for record in records}
        ),
        "inclusion_decision_sha256": sorted({record.decision_ref.sha256 for record in records}),
        "inclusion_decision_manifest_sha256": sorted(
            {record.decision_manifest_ref.sha256 for record in records}
        ),
        "schema_sha256": {
            ASSIGNED_UNITS_TABLE: schema_sha256(assigned_schema),
            ELIGIBLE_OUTCOMES_TABLE: schema_sha256(outcomes_schema),
        },
        "protocol_sha256": sorted({record.request.protocol_sha256 for record in records}),
        "population_id": sorted({record.assigned_unit.population_id for record in records}),
        "endpoint_id": sorted(
            {outcome.endpoint_id for record in records for outcome in record.eligible_outcomes}
        ),
        "stratum": sorted({record.request.matrix_key.stratum.value for record in records}),
        "history_mode": sorted(
            {record.request.matrix_key.history_mode.value for record in records}
        ),
        "environment_fingerprint_sha256": sorted(
            {record.request.frozen_hashes["environment_fingerprint"] for record in records}
        ),
        "model_sha256": sorted({record.request.frozen_hashes["model"] for record in records}),
        "runtime_lock_sha256": sorted({record.request.runtime_lock_sha256 for record in records}),
        "files": {
            ASSIGNED_UNITS_TABLE: _ref_document(assigned_ref),
            ELIGIBLE_OUTCOMES_TABLE: _ref_document(outcomes_ref),
        },
        "ordering": {
            ASSIGNED_UNITS_TABLE: _ordering_keys(assigned_schema),
            ELIGIBLE_OUTCOMES_TABLE: _ordering_keys(outcomes_schema),
        },
    }


def _derivation_manifest_document(
    dataset_manifest: Mapping[str, object],
    dataset_manifest_ref: ArtifactRef,
    assigned_ref: ArtifactRef,
    outcomes_ref: ArtifactRef,
) -> dict[str, object]:
    document = {
        "schema_version": MATERIALIZATION_SCHEMA_VERSION,
        "artifact_type": "parquet_derivation_manifest",
        "dataset_manifest_sha256": dataset_manifest_ref.sha256,
        "dataset_version": dataset_manifest["dataset_version"],
        "materialization_sha256": dataset_manifest["materialization_sha256"],
        "assigned_units_sha256": assigned_ref.sha256,
        "eligible_outcomes_sha256": outcomes_ref.sha256,
        "source_manifest_sha256": dataset_manifest["source_manifest_sha256"],
    }
    document["derivation_sha256"] = canonical_digest(document)
    return document


def _require_reader_agreement(first: pa.Table, second: pa.Table) -> None:
    if first.schema != second.schema or first.to_pylist() != second.to_pylist():
        raise MaterializationError("parquet_reader_disagreement")
    expected = {
        ASSIGNED_UNITS_TABLE: assigned_units_schema(),
        ELIGIBLE_OUTCOMES_TABLE: eligible_outcomes_schema(),
    }
    metadata = first.schema.metadata
    if metadata is None or b"memrelay.table" not in metadata:
        raise MaterializationError("parquet_schema_drift")
    table_name = metadata[b"memrelay.table"].decode()
    if table_name not in expected or first.schema != expected[table_name]:
        raise MaterializationError("parquet_schema_drift")


def _ordering_keys(schema: pa.Schema) -> list[str]:
    metadata = schema.metadata
    if metadata is None or b"memrelay.ordering_keys" not in metadata:
        raise MaterializationError("ordering_contract_missing")
    value = json.loads(metadata[b"memrelay.ordering_keys"].decode("utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise MaterializationError("ordering_contract_invalid")
    return value


def _load_canonical_json(path: Path) -> dict[str, object]:
    try:
        data = path.read_bytes()
        value = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MaterializationError("published_manifest_missing_or_invalid") from error
    if not isinstance(value, dict) or canonical_bytes(value) != data:
        raise MaterializationError("published_manifest_not_canonical")
    return value


def _write_bytes(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _destination_exists_collision(error: OSError, destination: Path) -> bool:
    """Recognize only platform-specific immutable-directory publication races."""
    return destination.is_dir() and (
        isinstance(error, FileExistsError)
        or error.errno in {errno.EACCES, errno.ENOTEMPTY}
        or getattr(error, "winerror", None) == 5
    )


def _decimal_quantity(value: Decimal | int) -> Decimal:
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise MaterializationError("cost_quantity_invalid") from error
    if not decimal.is_finite() or decimal < 0:
        raise MaterializationError("cost_quantity_invalid")
    return decimal


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


def _ref_document(reference: ArtifactRef) -> dict[str, object]:
    return {
        "artifact_id": str(reference.artifact_id),
        "sha256": reference.sha256,
        "size_bytes": reference.size_bytes,
    }


def _reference_from_document(document: Mapping[str, object], table_name: str) -> ArtifactRef:
    files = document.get("files")
    if not isinstance(files, Mapping):
        raise MaterializationError("published_dataset_manifest_invalid")
    value = files.get(table_name)
    if not isinstance(value, Mapping):
        raise MaterializationError("published_dataset_file_reference_missing")
    artifact_id = value.get("artifact_id")
    digest = value.get("sha256")
    size_bytes = value.get("size_bytes")
    if (
        not isinstance(artifact_id, str)
        or not isinstance(digest, str)
        or not _is_sha256(digest)
        or not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes < 0
    ):
        raise MaterializationError("published_dataset_file_reference_invalid")
    try:
        return ArtifactRef(ArtifactId(artifact_id), digest, size_bytes)
    except ValueError as error:
        raise MaterializationError("published_dataset_file_reference_invalid") from error
