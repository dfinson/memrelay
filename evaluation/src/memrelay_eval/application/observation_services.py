"""Application composition for path-scoped observation qualification."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from memrelay_eval.adapters.memrelay.observation import (
    ObservationQualificationDecision,
    ObservationQualificationService,
)
from memrelay_eval.adapters.memrelay.observation_runner import (
    ProductObservationRun,
    current_observation_identity,
    receipt_sha256,
    run_actual_observation_composition,
)
from memrelay_eval.adapters.telemetry.observation import observation_telemetry_evidence
from memrelay_eval.domain.observation import (
    ObservationBoundary,
    ObservationContract,
    ObservationEvidence,
    ObservationFailureReason,
    ObservationIdentity,
    ObservationPath,
    SentinelBoundaryRecord,
)


def resolve_product_observation_identity(
    *,
    path: ObservationPath,
    product_config_path: Path,
    runtime_lock_path: Path,
    workspace: Path,
) -> tuple[ObservationIdentity, Any]:
    """Resolve product identity through the application-owned composition boundary."""

    return current_observation_identity(
        path=path,
        product_config_path=product_config_path,
        runtime_lock_path=runtime_lock_path,
        workspace=workspace,
    )


def execute_product_observation_composition(
    *,
    contract: ObservationContract,
    config: Any,
    workspace: Path,
) -> ProductObservationRun:
    """Execute the repository adapter behind the evaluator application boundary."""

    return run_actual_observation_composition(
        contract=contract,
        config=config,
        workspace=workspace,
    )


def product_observation_receipt_sha256(run: ProductObservationRun) -> str:
    """Commit the native receipt through the composition boundary."""

    return receipt_sha256(run)


def qualify_observation(
    contract: ObservationContract,
    evidence: ObservationEvidence,
    *,
    decided_at: datetime,
) -> ObservationQualificationDecision:
    """Apply the observation adapter without exposing adapters to the CLI boundary."""

    return ObservationQualificationService().qualify(
        contract,
        evidence,
        decided_at=decided_at,
    )


def verified_product_observation_evidence(
    contract: ObservationContract,
    run: ProductObservationRun,
    *,
    native_receipt_persisted: bool,
) -> ObservationEvidence:
    """Reconcile telemetry emitted by the just-executed product composition."""

    telemetry = observation_telemetry_evidence(
        run.telemetry_spans,
        path=contract.path,
        expected_sentinels=contract.expected_sentinels,
        collector_shutdown_verified=run.collector_shutdown_verified,
    )
    observed_at = _retained_observation_times(contract, run)
    manifest_records = (
        tuple(
            SentinelBoundaryRecord(
                path=contract.path,
                boundary=ObservationBoundary.MANIFEST,
                sentinel_id=sentinel.identifier,
                sequence=sentinel.sequence,
                observed_at=observed_at[sentinel.identifier],
                restart_epoch=1,
            )
            for sentinel in contract.expected_sentinels
            if sentinel.identifier in observed_at
        )
        if native_receipt_persisted
        else ()
    )
    reconciled = (
        run.reconciliation_completed
        and telemetry.complete
        and native_receipt_persisted
        and not telemetry.failure_codes
    )
    reconciliation_records = (
        tuple(
            SentinelBoundaryRecord(
                path=contract.path,
                boundary=ObservationBoundary.RECONCILIATION,
                sentinel_id=sentinel.identifier,
                sequence=sentinel.sequence,
                observed_at=observed_at[sentinel.identifier],
                restart_epoch=1,
            )
            for sentinel in contract.expected_sentinels
            if sentinel.identifier in observed_at
        )
        if reconciled
        else ()
    )
    return ObservationEvidence(
        path=contract.path,
        conformance_sha256=contract.identity.conformance_sha256,
        records=(
            run.native_records + telemetry.records + manifest_records + reconciliation_records
        ),
        final_drain_completed=run.final_drain_completed,
        collector_shutdown_verified=run.collector_shutdown_verified and telemetry.complete,
        reconciliation_completed=reconciled,
        authority_conflict=run.authority_conflict or bool(telemetry.failure_codes),
        partial_success=run.partial_success or not reconciled,
        evidence_failure_reasons=(
            run.evidence_failure_reasons + _telemetry_failure_reasons(telemetry.failure_codes)
        ),
    )


def _retained_observation_times(
    contract: ObservationContract, run: ProductObservationRun
) -> dict[str, datetime]:
    """Use retained native product times rather than relabeling post-run evidence."""

    times: dict[str, datetime] = {}
    expected = set(contract.expected_identifiers)
    for record in run.native_records:
        if record.sentinel_id in expected:
            prior = times.get(record.sentinel_id)
            if prior is None or record.observed_at > prior:
                times[record.sentinel_id] = record.observed_at
    return times


def _telemetry_failure_reasons(
    failure_codes: tuple[str, ...],
) -> tuple[ObservationFailureReason, ...]:
    """Map collector reconciliation facts to stable path-qualification reasons."""

    reasons: set[ObservationFailureReason] = set()
    if "TEL-OBSERVATION-DROP" in failure_codes or "TEL-DROP" in failure_codes:
        reasons.add(ObservationFailureReason.TELEMETRY_DELIVERY_LOSS)
    if "TEL-OBSERVATION-MALFORMED" in failure_codes:
        reasons.add(ObservationFailureReason.TELEMETRY_MALFORMED)
    if "TEL-OBSERVATION-PATH-MISMATCH" in failure_codes:
        reasons.add(ObservationFailureReason.TELEMETRY_PATH_MISMATCH)
    if failure_codes and not reasons:
        reasons.add(ObservationFailureReason.UNRECONCILED)
    return tuple(sorted(reasons, key=lambda item: item.value))
