from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from memrelay_eval.adapters.memrelay.observation import build_observation_identity
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.cli.main import main
from memrelay_eval.domain.errors import ObservationQualificationError
from memrelay_eval.domain.observation import (
    ObservationBoundary,
    ObservationContract,
    ObservationEvidence,
    ObservationFailureReason,
    ObservationPath,
    ObservationSentinel,
    SentinelBoundaryRecord,
    assess_observation,
)

_STARTED_AT = datetime(2026, 8, 11, tzinfo=UTC)


def _contract() -> ObservationContract:
    identity = build_observation_identity(
        source_files=(
            Path(__file__).resolve().parents[3]
            / "src"
            / "memrelay"
            / "daemon"
            / "session_discovery.py",
        ),
        semantic_map={"observation": "synthetic-v1"},
        configuration={"ingest": {"intake_source": "replay"}},
        runtime_lock=b"synthetic-runtime-lock-v1",
    )
    return ObservationContract(
        path=ObservationPath.REPLAY,
        identity=identity,
        expected_sentinels=tuple(
            ObservationSentinel(f"sentinel_{sequence:032x}", sequence) for sequence in range(1, 4)
        ),
        window_started_at=_STARTED_AT,
        deadline_at=_STARTED_AT + timedelta(minutes=1),
    )


def _evidence(contract: ObservationContract) -> ObservationEvidence:
    records = tuple(
        SentinelBoundaryRecord(
            path=contract.path,
            boundary=boundary,
            sentinel_id=sentinel.identifier,
            sequence=sentinel.sequence,
            observed_at=_STARTED_AT + timedelta(seconds=sentinel.sequence),
            restart_epoch=1 if sentinel.sequence == 3 else 0,
        )
        for boundary in ObservationBoundary
        for sentinel in contract.expected_sentinels
    )
    return ObservationEvidence(
        path=contract.path,
        conformance_sha256=contract.identity.conformance_sha256,
        records=records,
        final_drain_completed=True,
        collector_shutdown_verified=True,
        reconciliation_completed=True,
    )


def _assert_blocked(evidence: ObservationEvidence, reason: ObservationFailureReason) -> None:
    assessment = assess_observation(_contract(), evidence)
    assert reason in assessment.failure_reasons
    assert not assessment.qualified
    with pytest.raises(ObservationQualificationError):
        assessment.completeness_claim()


def test_missing_interior_sentinel_is_retained_as_missing_and_gap() -> None:
    contract = _contract()
    evidence = _evidence(contract)
    records = tuple(
        record
        for record in evidence.records
        if not (
            record.boundary is ObservationBoundary.SPOOL
            and record.sentinel_id == contract.expected_sentinels[1].identifier
        )
    )

    assessment = assess_observation(contract, replace(evidence, records=records))
    assert {
        ObservationFailureReason.MISSING,
        ObservationFailureReason.GAP,
    }.issubset(assessment.failure_reasons)
    assert assessment.missing_by_boundary[ObservationBoundary.SPOOL] == (
        contract.expected_sentinels[1].identifier,
    )


def test_post_idempotency_duplicate_fails_and_retains_duplicate_identifier() -> None:
    contract = _contract()
    evidence = _evidence(contract)
    duplicate = next(
        record
        for record in evidence.records
        if record.boundary is ObservationBoundary.SPOOL and record.sequence == 1
    )

    assessment = assess_observation(
        contract, replace(evidence, records=evidence.records + (duplicate,))
    )
    assert ObservationFailureReason.DUPLICATED in assessment.failure_reasons
    assert assessment.post_idempotency_duplicates == (duplicate.sentinel_id,)


def test_reordered_boundary_fails_closed() -> None:
    contract = _contract()
    evidence = _evidence(contract)
    daemon = [
        record for record in evidence.records if record.boundary is ObservationBoundary.DAEMON
    ]
    other = [
        record for record in evidence.records if record.boundary is not ObservationBoundary.DAEMON
    ]
    reordered = tuple(other + [daemon[1], daemon[0], daemon[2]])

    assessment = assess_observation(contract, replace(evidence, records=reordered))
    assert ObservationFailureReason.REORDERED in assessment.failure_reasons
    assert ObservationBoundary.DAEMON in assessment.reordered_boundaries


def test_delayed_sentinel_fails_against_the_frozen_deadline() -> None:
    contract = _contract()
    evidence = _evidence(contract)
    delayed = next(
        record for record in evidence.records if record.boundary is ObservationBoundary.MCP_GRAPH
    )
    records = tuple(
        replace(record, observed_at=contract.deadline_at + timedelta(microseconds=1))
        if record is delayed
        else record
        for record in evidence.records
    )

    assessment = assess_observation(contract, replace(evidence, records=records))
    assert ObservationFailureReason.DELAYED in assessment.failure_reasons
    assert delayed.sentinel_id in assessment.delayed_sentinels


@pytest.mark.parametrize(
    ("alter", "reason"),
    [
        (
            lambda evidence: replace(evidence, reconciliation_completed=False),
            ObservationFailureReason.UNRECONCILED,
        ),
        (
            lambda evidence: replace(evidence, final_drain_completed=False),
            ObservationFailureReason.TERMINAL_FLUSH_MISSING,
        ),
        (
            lambda evidence: replace(
                evidence,
                records=tuple(replace(record, restart_epoch=0) for record in evidence.records),
            ),
            ObservationFailureReason.RESTART_GAP,
        ),
    ],
)
def test_unreconciled_shutdown_and_restart_faults_block_path_claims(alter, reason) -> None:
    contract = _contract()
    _assert_blocked(alter(_evidence(contract)), reason)


def test_unqualified_cli_decision_is_persisted_with_the_typed_failure_manifest(
    tmp_path: Path,
) -> None:
    contract = _contract()
    evidence = replace(_evidence(contract), reconciliation_completed=False)
    input_path = tmp_path / "observation-input.json"
    input_path.write_bytes(
        canonical_bytes(
            {
                "contract": contract.to_document(),
                "evidence": evidence.to_document(),
                "decided_at": (_STARTED_AT + timedelta(minutes=2))
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
            }
        )
    )
    output_root = tmp_path / "artifacts"

    with pytest.raises(ObservationQualificationError) as error:
        main(
            [
                "observation-conformance",
                "--input",
                str(input_path),
                "--output-root",
                str(output_root),
            ]
        )

    assert error.value.code == ObservationFailureReason.UNRECONCILED.value
    decision_path = next(output_root.glob("observation-qualification/replay/*/decision-*.json"))
    assert json.loads(decision_path.read_text(encoding="utf-8"))["qualified"] is False
    command_manifest_path = next(output_root.glob("commands/observation-conformance-*.json"))
    command_manifest = json.loads(command_manifest_path.read_text(encoding="utf-8"))
    assert command_manifest["error_code"] == ObservationFailureReason.UNRECONCILED.value
