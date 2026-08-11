"""Contracts for immutable executable bootstrap/conformance authority."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.evidence.conformance import (
    CONFORMANCE_LABEL,
    REQUIRED_PROOF_IDS,
    REQUIRED_STAGE_LOCKS,
    ConformanceContext,
    ConformanceProbe,
    ProofRegistry,
    build_bootstrap_receipt,
    build_conformance_report,
    load_bootstrap_receipt,
    load_conformance_report,
    observed_probe_result,
    report_bytes,
    write_bootstrap_receipt,
    write_conformance_report,
)

HASH_A = "a" * 64


def _locks() -> dict[str, str]:
    return dict.fromkeys(REQUIRED_STAGE_LOCKS, HASH_A)


def _bootstrap() -> bytes:
    return writeable_bootstrap()


def writeable_bootstrap() -> bytes:
    from memrelay_eval.evidence.conformance import bootstrap_receipt_bytes

    return bootstrap_receipt_bytes(
        build_bootstrap_receipt(
            mode="unpaid_ci",
            runtime_lock={"lock_sha256": HASH_A},
            input_hashes={"runtime": HASH_A},
            output_hashes={"telemetry": HASH_A},
            environment_sha256=HASH_A,
            protocol_sha256=HASH_A,
        )
    )


def _receipts() -> tuple[object, ...]:
    counts: dict[str, int] = {}

    def probe(proof_id: str):
        def execute(_: ConformanceContext):
            counts[proof_id] = counts.get(proof_id, 0) + 1
            return observed_probe_result(
                input_documents={"input": {"proof_id": proof_id}},
                output_documents={"output": {"proof_id": proof_id}},
            )

        return execute

    registry = ProofRegistry(
        tuple(
            ConformanceProbe(proof_id, f"test/{proof_id}", probe(proof_id))
            for proof_id in REQUIRED_PROOF_IDS
        )
    )
    receipts = registry.execute(
        ConformanceContext(
            mode="unpaid_ci",
            evaluation_root=Path(__file__).parents[2],
            run_root=Path(__file__).parent,
            stage_locks=_locks(),
            bootstrap_receipt=load_bootstrap_receipt(_bootstrap()),
        )
    )
    assert counts == dict.fromkeys(REQUIRED_PROOF_IDS, 1)
    return receipts


def _report() -> dict[str, object]:
    bootstrap = _bootstrap()
    return build_conformance_report(
        mode="unpaid_ci",
        stage_locks=_locks(),
        proof_receipts=_receipts(),  # type: ignore[arg-type]
        input_hashes={"catalog_to_report_sha256": HASH_A},
        bootstrap_receipt_sha256=sha256(bootstrap).hexdigest(),
    )


def test_complete_report_is_canonical_outcome_blind_and_idempotent(tmp_path: Path) -> None:
    report = _report()
    payload = report_bytes(report)

    loaded = load_conformance_report(payload)
    first = write_conformance_report(tmp_path, report)
    second = write_conformance_report(tmp_path, report)

    assert loaded["evidence_label"] == CONFORMANCE_LABEL
    assert loaded["status"] == "passed"
    assert {item["proof_id"] for item in loaded["proof_receipts"]} == REQUIRED_PROOF_IDS
    assert first == second
    assert first.read_bytes() == payload


def test_each_receipt_has_proof_specific_observed_input_and_output_hashes() -> None:
    receipts = _receipts()

    assert len({receipt.input_hashes["input"] for receipt in receipts}) == len(REQUIRED_PROOF_IDS)
    assert len({receipt.output_hashes["output"] for receipt in receipts}) == len(REQUIRED_PROOF_IDS)


def test_bootstrap_receipt_is_immutable_and_rejects_hardlinks(tmp_path: Path) -> None:
    receipt = build_bootstrap_receipt(
        mode="unpaid_ci",
        runtime_lock={"lock_sha256": HASH_A},
        input_hashes={"runtime": HASH_A},
        output_hashes={"telemetry": HASH_A},
        environment_sha256=HASH_A,
        protocol_sha256=HASH_A,
    )
    path = write_bootstrap_receipt(tmp_path, receipt)
    assert path.read_bytes()
    linked = path.with_name("linked.json")
    linked.hardlink_to(path)

    with pytest.raises(Exception, match="existing authority is unsafe"):
        write_bootstrap_receipt(tmp_path, receipt)


def test_report_matches_the_published_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = Path(__file__).parents[2] / "schemas" / "conformance-report.schema.json"

    jsonschema.validate(_report(), json.loads(schema_path.read_text(encoding="utf-8")))
