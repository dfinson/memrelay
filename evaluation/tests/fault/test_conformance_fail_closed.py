"""Failure modes for observed conformance authority and enrollment admission."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.domain.errors import ConformancePauseError, StageControlError
from memrelay_eval.evidence.conformance import (
    REQUIRED_PROOF_IDS,
    REQUIRED_STAGE_LOCKS,
    ConformanceContext,
    ConformanceProbe,
    ProofRegistry,
    build_bootstrap_receipt,
    build_conformance_report,
    failed_probe_result,
    observed_probe_result,
    report_bytes,
    require_enrollment_conformance,
)


def _locks() -> dict[str, str]:
    return dict.fromkeys(REQUIRED_STAGE_LOCKS, "a" * 64)


def _bootstrap() -> bytes:
    from memrelay_eval.evidence.conformance import bootstrap_receipt_bytes

    return bootstrap_receipt_bytes(
        build_bootstrap_receipt(
            mode="unpaid_ci",
            runtime_lock={"lock_sha256": "a" * 64},
            input_hashes={"runtime": "a" * 64},
            output_hashes={"telemetry": "a" * 64},
            environment_sha256="a" * 64,
            protocol_sha256="a" * 64,
        )
    )


def _receipts(*, failed: bool = False, noop: bool = False):
    def execute(proof_id: str):
        if noop:
            return lambda _: None
        if failed and proof_id == "CAS-CORRUPTION":
            return lambda _: failed_probe_result("corruption_detected", {"proof_id": proof_id})
        return lambda _: observed_probe_result(
            input_documents={"proof": proof_id},
            output_documents={"observed": proof_id},
        )

    registry = ProofRegistry(
        tuple(
            ConformanceProbe(proof_id, f"test/{proof_id}", execute(proof_id))
            for proof_id in REQUIRED_PROOF_IDS
        )
    )
    return registry.execute(
        ConformanceContext(
            mode="unpaid_ci",
            evaluation_root=Path(__file__).parents[2],
            run_root=Path(__file__).parent,
            stage_locks=_locks(),
            bootstrap_receipt={},
        )
    )


def _passed_report() -> tuple[bytes, bytes]:
    bootstrap = _bootstrap()
    report = build_conformance_report(
        mode="unpaid_ci",
        stage_locks=_locks(),
        proof_receipts=_receipts(),
        input_hashes={"catalog_to_report_sha256": "a" * 64},
        bootstrap_receipt_sha256=sha256(bootstrap).hexdigest(),
    )
    return report_bytes(report), bootstrap


def test_tampered_or_stale_report_blocks_enrollment() -> None:
    payload, bootstrap = _passed_report()
    tampered = payload.replace(b'"status":"passed"', b'"status":"blocked"', 1)

    with pytest.raises(ConformancePauseError) as failure:
        require_enrollment_conformance(tampered, bootstrap_data=bootstrap, stage_locks=_locks())
    assert failure.value.code == "conformance_report_corrupt"

    stale = _locks()
    stale["runtime_lock_sha256"] = "b" * 64
    with pytest.raises(StageControlError) as failure:
        require_enrollment_conformance(payload, bootstrap_data=bootstrap, stage_locks=stale)
    assert failure.value.code == "conformance_report_stale"


def test_failed_observation_never_becomes_enrollment_authority() -> None:
    bootstrap = _bootstrap()
    report = build_conformance_report(
        mode="unpaid_ci",
        stage_locks=_locks(),
        proof_receipts=_receipts(failed=True),
        input_hashes={"catalog_to_report_sha256": "a" * 64},
        bootstrap_receipt_sha256=sha256(bootstrap).hexdigest(),
    )

    with pytest.raises(ConformancePauseError) as failure:
        require_enrollment_conformance(
            report_bytes(report), bootstrap_data=bootstrap, stage_locks=_locks()
        )
    assert failure.value.code == "conformance_proof_failed"


def test_noop_probe_cannot_produce_a_receipt() -> None:
    with pytest.raises(ConformancePauseError) as failure:
        _receipts(noop=True)
    assert failure.value.code == "conformance_probe_no_observation"
