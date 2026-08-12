"""Failure modes for observed conformance authority and enrollment admission."""

from __future__ import annotations

import sys
from hashlib import sha256
from pathlib import Path

import memrelay_eval.evidence.conformance as conformance_evidence
import pytest
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.errors import ConformancePauseError, StageControlError
from memrelay_eval.evidence.conformance import (
    PYTEST_DIAGNOSTIC_LIMIT,
    REQUIRED_PROOF_IDS,
    REQUIRED_STAGE_LOCKS,
    ConformanceContext,
    ConformanceProbe,
    ProofRegistry,
    build_bootstrap_receipt,
    build_conformance_report,
    failed_probe_result,
    observed_probe_result,
    pytest_probe,
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


def _context() -> ConformanceContext:
    return ConformanceContext(
        mode="unpaid_ci",
        evaluation_root=Path(__file__).parents[2],
        run_root=Path(__file__).parent,
        stage_locks=_locks(),
        bootstrap_receipt={},
    )


def test_real_pytest_failure_retains_typed_execution_and_bounded_diagnostics(
    tmp_path: Path,
) -> None:
    target = tmp_path / "test_intentional_failure.py"
    target.write_text(
        "def test_intentional_failure():\n"
        "    print('x' * 20000)\n"
        "    assert False, 'intentional probe failure'\n",
        encoding="utf-8",
    )

    result = pytest_probe(str(target))(_context())

    command = (sys.executable, "-m", "pytest", "-q", str(target))
    assert result.status == "failed"
    assert result.terminal == "pytest_contract_failed"
    assert result.failure_evidence_sha256 is not None
    assert (
        result.input_hashes["pytest_command"]
        == sha256(canonical_bytes({"command": command, "target": str(target)})).hexdigest()
    )
    assert (
        result.input_hashes["pytest_mode"]
        == sha256(canonical_bytes({"mode": "unpaid_ci"})).hexdigest()
    )
    assert (
        result.output_hashes["pytest_returncode"]
        == sha256(canonical_bytes({"returncode": 1})).hexdigest()
    )
    assert result.output_hashes["pytest_diagnostic"] != result.output_hashes["pytest_output"]
    assert PYTEST_DIAGNOSTIC_LIMIT == 16 * 1024


def test_pytest_launch_exception_is_unavailable_not_a_test_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_: object, **__: object) -> object:
        raise OSError("pytest executable unavailable")

    monkeypatch.setattr(conformance_evidence.subprocess, "run", unavailable)

    result = pytest_probe("tests/unit/catalog/test_validation.py")(_context())

    assert result.status == "unavailable"
    assert result.terminal == "pytest_execution_unavailable"
    assert result.failure_evidence_sha256 == result.output_hashes["pytest_launch_error"]
    assert "pytest_returncode" not in result.output_hashes
