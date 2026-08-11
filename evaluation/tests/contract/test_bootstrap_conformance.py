"""Contracts for the immutable bootstrap/conformance authority."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.evidence.conformance import (
    CONFORMANCE_LABEL,
    REQUIRED_PROOF_IDS,
    REQUIRED_STAGE_LOCKS,
    build_conformance_report,
    load_conformance_report,
    report_bytes,
    synthetic_proof_receipts,
    write_conformance_report,
)

HASH_A = "a" * 64


def _locks() -> dict[str, str]:
    return dict.fromkeys(REQUIRED_STAGE_LOCKS, HASH_A)


def _report() -> dict[str, object]:
    root = Path(__file__).parents[2]
    return build_conformance_report(
        mode="unpaid_ci",
        stage_locks=_locks(),
        proof_receipts=synthetic_proof_receipts(input_sha256=HASH_A, implementation_root=root),
        input_hashes={"synthetic_catalog_to_report_sha256": HASH_A},
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
    assert sha256(payload).hexdigest() != str(loaded["report_id"])


def test_report_matches_the_published_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = Path(__file__).parents[2] / "schemas" / "conformance-report.schema.json"

    jsonschema.validate(_report(), json.loads(schema_path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("field", REQUIRED_STAGE_LOCKS)
def test_any_bound_lock_change_creates_a_distinct_report_identity(field: str) -> None:
    original = _report()
    locks = _locks()
    locks[field] = "b" * 64
    changed = build_conformance_report(
        mode="unpaid_ci",
        stage_locks=locks,
        proof_receipts=synthetic_proof_receipts(
            input_sha256=HASH_A, implementation_root=Path(__file__).parents[2]
        ),
        input_hashes={"synthetic_catalog_to_report_sha256": HASH_A},
    )

    assert changed["report_id"] != original["report_id"]
