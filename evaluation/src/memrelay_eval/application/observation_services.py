"""Application composition for path-scoped observation qualification."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from memrelay_eval.adapters.memrelay.observation import (
    ObservationQualificationDecision,
    ObservationQualificationService,
)
from memrelay_eval.adapters.memrelay.observation_runner import ProductObservationRun
from memrelay_eval.adapters.telemetry.observation import observation_telemetry_evidence
from memrelay_eval.adapters.telemetry.semantics import (
    SpanClass,
    TelemetryContext,
    TelemetrySpan,
)
from memrelay_eval.domain.identity import identity_for_span_class
from memrelay_eval.domain.observation import (
    ObservationBoundary,
    ObservationContract,
    ObservationEvidence,
    SentinelBoundaryRecord,
)


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
    """Build evaluator evidence only from the just-executed native composition result."""

    spans = tuple(
        _observation_span(contract, sentinel.identifier, sentinel.sequence)
        for sentinel in contract.expected_sentinels
    )
    telemetry = observation_telemetry_evidence(
        spans,
        path=contract.path,
        collector_shutdown_verified=run.collector_shutdown_verified,
    )
    observed_at = datetime.now(UTC)
    manifest_records = (
        tuple(
            SentinelBoundaryRecord(
                path=contract.path,
                boundary=ObservationBoundary.MANIFEST,
                sentinel_id=sentinel.identifier,
                sequence=sentinel.sequence,
                observed_at=observed_at,
                restart_epoch=1,
            )
            for sentinel in contract.expected_sentinels
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
                observed_at=observed_at,
                restart_epoch=1,
            )
            for sentinel in contract.expected_sentinels
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
    )


def _observation_span(
    contract: ObservationContract, identifier: str, sequence: int
) -> TelemetrySpan:
    """Emit a value-safe telemetry receipt only after native daemon/MCP verification."""

    digest = sha256(
        f"{contract.identity.conformance_sha256}:{contract.path.value}:{identifier}".encode("ascii")
    ).hexdigest()
    context = TelemetryContext(
        experiment_id=f"exp_{digest[:32]}",
        protocol_id=f"protocol_{digest[:32]}",
        run_id=f"run_{digest[:32]}",
        attempt_id=f"attempt_{digest[:32]}",
        scenario_id=f"scenario_{digest[:32]}",
        stratum_id="product",
        history_mode="controlled",
        identity=identity_for_span_class(SpanClass.DAEMON_DISPATCH.value),
        evidence_class="observation_sentinel",
        exposure_state="unexposed",
        environment_fingerprint_sha256=digest,
    )
    observed_at = datetime.now(UTC)
    return TelemetrySpan(
        span_id=f"span-{digest[:32]}",
        span_class=SpanClass.DAEMON_DISPATCH,
        context=context,
        started_at=observed_at,
        ended_at=observed_at,
        attributes={
            "sentinel_id": identifier,
            "sentinel_sequence": sequence,
            "observation_path": contract.path.value,
            "restart_epoch": 1,
        },
    )
