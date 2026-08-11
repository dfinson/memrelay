from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pyarrow.parquet as pq
from memrelay_eval.adapters.artifacts.filesystem import FilesystemArtifactStore
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.states import InclusionStatus
from memrelay_eval.evidence.parquet import (
    AssignedUnit,
    CostObservation,
    EligibleOutcome,
    ParquetMaterializer,
    ReconciledTerminalRecord,
    seal_eligible_outcome_authority,
)
from memrelay_eval.evidence.reconcile import ReconciliationBlocker, ReconciliationService
from memrelay_eval.ledger import SqliteLedger
from tests.integration.evidence.test_reconcile_inclusion import (
    _record_authority,
    _request,
    _seed_scored,
)


def _record(tmp_path) -> tuple[FilesystemArtifactStore, ReconciledTerminalRecord]:
    store = FilesystemArtifactStore(tmp_path / "source-artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(ledger)
    request = _request(store, run, attempt)
    _record_authority(ledger, request)
    result = ReconciliationService(store, ledger).reconcile(request)
    ledger.close()
    return (
        store,
        ReconciledTerminalRecord(
            request=request,
            report=result.report,
            report_ref=result.report_ref,
            report_manifest_ref=result.report_manifest_ref,
            decision=result.decision,
            decision_ref=result.decision_ref,
            decision_manifest_ref=result.decision_manifest_ref,
            assigned_unit=AssignedUnit(
                analysis_unit_id="analysis_" + "a" * 32,
                population_id="population_v1",
                attrition_status="complete",
                exposure_status="exposed",
                contamination_status="isolated",
                failure_reason=None,
                costs=(
                    CostObservation(
                        "framework_openai",
                        "usd",
                        Decimal("0.0000000000"),
                        "observed",
                    ),
                    CostObservation("copilot_subscription", "input_token", None, "unavailable"),
                ),
            ),
            eligible_outcomes=(
                seal_eligible_outcome_authority(
                    store,
                    EligibleOutcome(
                        endpoint_id="endpoint_" + "b" * 32,
                        outcome_id="outcome_" + "c" * 32,
                        outcome_kind="numeric",
                        numeric_value=0.123456789012,
                        unit="proportion",
                        evidence_refs=(result.report_ref,),
                    ),
                    request,
                ),
            ),
        ),
    )


def test_materialization_writes_complete_itt_denominator_and_confirmatory_outcomes(
    tmp_path,
) -> None:
    store, record = _record(tmp_path)
    result = ParquetMaterializer(store, tmp_path / "parquet").materialize((record,))

    assigned = pq.read_table(result.directory / "assigned_units.parquet")
    outcomes = pq.ParquetFile(result.directory / "eligible_outcomes.parquet").read()
    assigned_row = assigned.to_pylist()[0]
    outcome_row = outcomes.to_pylist()[0]

    assert result.dataset_version.startswith("parquet-v1.0.0-")
    assert assigned_row["inclusion_status"] == "included"
    assert assigned_row["costs"][0]["quantity"] == Decimal("0E-10")
    assert assigned_row["costs"][1]["quantity"] is None
    assert assigned_row["costs"][1]["availability"] == "unavailable"
    assert outcome_row["numeric_value"] == 0.123456789012
    assert outcome_row["reconciliation_sha256"] == record.decision.reconciliation_sha256
    assert (result.directory / "dataset-manifest.json").is_file()
    assert (result.directory / "derivation-manifest.json").is_file()


def test_identical_inputs_reuse_version_and_changed_input_creates_new_version(tmp_path) -> None:
    store, record = _record(tmp_path)
    materializer = ParquetMaterializer(store, tmp_path / "parquet")

    first = materializer.materialize((record,))
    repeated = materializer.materialize((record,))
    changed_unit = replace(record.assigned_unit, population_id="population_v2")
    changed = materializer.materialize((replace(record, assigned_unit=changed_unit),))

    assert repeated.dataset_version == first.dataset_version
    assert repeated.assigned_units_ref == first.assigned_units_ref
    assert changed.dataset_version != first.dataset_version
    assert changed.directory != first.directory


def test_reordered_outcomes_materialize_to_the_same_canonical_dataset(tmp_path) -> None:
    store, record = _record(tmp_path)
    second_outcome = seal_eligible_outcome_authority(
        store,
        EligibleOutcome(
            endpoint_id="endpoint_" + "a" * 32,
            outcome_id="outcome_" + "a" * 32,
            outcome_kind="categorical",
            category_value="passed",
            unit="grade",
            evidence_refs=(record.report_ref,),
        ),
        record.request,
    )
    ordered = replace(record, eligible_outcomes=(second_outcome, *record.eligible_outcomes))
    reversed_input = replace(
        record,
        eligible_outcomes=tuple(reversed((second_outcome, *record.eligible_outcomes))),
    )

    first = ParquetMaterializer(store, tmp_path / "parquet-one").materialize((ordered,))
    second = ParquetMaterializer(store, tmp_path / "parquet-two").materialize((reversed_input,))

    assert first.dataset_version == second.dataset_version
    assert (first.directory / "eligible_outcomes.parquet").read_bytes() == (
        second.directory / "eligible_outcomes.parquet"
    ).read_bytes()


def test_excluded_assignment_remains_but_its_measurement_never_enters_eligible_table(
    tmp_path,
) -> None:
    store, record = _record(tmp_path)
    excluded_report = replace(
        record.report,
        blockers=(ReconciliationBlocker.TAMPER,),
        reconciliation_sha256="0" * 64,
    )
    reconciliation_sha256 = canonical_digest(excluded_report.basis_document())
    excluded_report = replace(excluded_report, reconciliation_sha256=reconciliation_sha256)
    excluded_decision = replace(
        record.decision,
        status=InclusionStatus.EXCLUDED,
        reason="tamper",
        reconciliation_sha256=reconciliation_sha256,
    )
    report_bytes = canonical_bytes(excluded_report.to_document())
    report_ref = store.put_bytes(
        report_bytes,
        media_type="application/json",
        classification="reconciliation_report",
    )

    # The excluded report is deliberately a new immutable terminal decision bundle.
    from memrelay_eval.domain.entities import ArtifactManifest
    from memrelay_eval.domain.states import ArtifactScope

    report_manifest_ref = store.write_manifest(
        ArtifactManifest(
            artifact_id=report_ref.artifact_id,
            kind="reconciliation_report",
            sha256=report_ref.sha256,
            size_bytes=report_ref.size_bytes,
            media_type="application/json",
            created_at=record.request.reconciled_at,
            producer_component="evidence_reconcile",
            producer_version="1.0.0",
            classification="reconciliation_report",
            contains_secrets=False,
            source_artifact_ids=tuple(
                evidence.artifact.artifact_id
                for evidence in record.request.evidence
                if evidence.artifact is not None
            ),
            retention_policy_id=record.request.retention_policy_id,
            encryption=None,
            scope=ArtifactScope.ATTEMPT,
            run_id=record.request.run_id,
            attempt_id=record.request.attempt_id,
        )
    )
    decision_bytes = canonical_bytes(
        {
            "schema_version": "1.0.0",
            "artifact_type": "inclusion_decision",
            "inclusion_id": str(excluded_decision.id),
            "run_id": str(excluded_decision.run_id),
            "status": "excluded",
            "reason": "tamper",
            "reconciliation_sha256": reconciliation_sha256,
            "occurred_at": "2026-08-10T12:00:00Z",
            "reconciliation_report_sha256": report_ref.sha256,
        }
    )
    decision_ref = store.put_bytes(
        decision_bytes,
        media_type="application/json",
        classification="inclusion_decision",
    )
    decision_manifest_ref = store.write_manifest(
        ArtifactManifest(
            artifact_id=decision_ref.artifact_id,
            kind="inclusion_decision",
            sha256=decision_ref.sha256,
            size_bytes=decision_ref.size_bytes,
            media_type="application/json",
            created_at=record.request.reconciled_at,
            producer_component="evidence_reconcile",
            producer_version="1.0.0",
            classification="inclusion_decision",
            contains_secrets=False,
            source_artifact_ids=(report_ref.artifact_id,),
            retention_policy_id=record.request.retention_policy_id,
            encryption=None,
            scope=ArtifactScope.ATTEMPT,
            run_id=record.request.run_id,
            attempt_id=record.request.attempt_id,
        )
    )
    excluded = ReconciledTerminalRecord(
        request=record.request,
        report=excluded_report,
        report_ref=report_ref,
        report_manifest_ref=report_manifest_ref,
        decision=excluded_decision,
        decision_ref=decision_ref,
        decision_manifest_ref=decision_manifest_ref,
        assigned_unit=record.assigned_unit,
        eligible_outcomes=(),
    )
    result = ParquetMaterializer(store, tmp_path / "parquet").materialize((excluded,))

    assert pq.read_table(result.directory / "assigned_units.parquet").num_rows == 1
    assert pq.read_table(result.directory / "eligible_outcomes.parquet").num_rows == 0
