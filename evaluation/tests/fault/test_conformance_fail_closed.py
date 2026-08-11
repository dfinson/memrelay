"""Failure modes for conformance authority and enrollment admission."""

from __future__ import annotations

from pathlib import Path

import pytest
from memrelay_eval.domain.errors import ConformancePauseError, StageControlError
from memrelay_eval.evidence.conformance import (
    REQUIRED_STAGE_LOCKS,
    build_conformance_report,
    report_bytes,
    require_enrollment_conformance,
    synthetic_proof_receipts,
)


def _locks() -> dict[str, str]:
    return dict.fromkeys(REQUIRED_STAGE_LOCKS, "a" * 64)


def _passed_report() -> bytes:
    report = build_conformance_report(
        mode="unpaid_ci",
        stage_locks=_locks(),
        proof_receipts=synthetic_proof_receipts(
            input_sha256="a" * 64, implementation_root=Path(__file__).parents[2]
        ),
        input_hashes={"synthetic_catalog_to_report_sha256": "a" * 64},
    )
    return report_bytes(report)


def test_tampered_or_stale_report_blocks_enrollment() -> None:
    payload = _passed_report()
    tampered = payload.replace(b'"status":"passed"', b'"status":"blocked"', 1)

    with pytest.raises(ConformancePauseError) as failure:
        require_enrollment_conformance(tampered, _locks())
    assert failure.value.code == "conformance_report_corrupt"

    stale = _locks()
    stale["runtime_lock_sha256"] = "b" * 64
    with pytest.raises(StageControlError) as failure:
        require_enrollment_conformance(payload, stale)
    assert failure.value.code == "conformance_report_stale"


def test_failed_receipt_never_becomes_an_enrollment_authority() -> None:
    receipts = list(
        synthetic_proof_receipts(
            input_sha256="a" * 64, implementation_root=Path(__file__).parents[2]
        )
    )
    failed = receipts[0]
    from memrelay_eval.evidence.conformance import proof_receipt

    receipts[0] = proof_receipt(
        failed.proof_id,
        implementation_sha256=failed.implementation_sha256,
        input_sha256=failed.input_sha256,
        status="failed",
    )
    report = build_conformance_report(
        mode="unpaid_ci",
        stage_locks=_locks(),
        proof_receipts=tuple(receipts),
        input_hashes={"synthetic_catalog_to_report_sha256": "a" * 64},
    )

    with pytest.raises(ConformancePauseError) as failure:
        require_enrollment_conformance(report_bytes(report), _locks())
    assert failure.value.code == "conformance_proof_failed"
