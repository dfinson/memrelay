"""Credential-free catalog-to-report conformance integration coverage."""

from __future__ import annotations

import json
import socket
from argparse import Namespace
from pathlib import Path

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.cli.commands import conformance
from memrelay_eval.evidence.conformance import (
    REQUIRED_PROOF_IDS,
    REQUIRED_STAGE_LOCKS,
    ConformanceProbe,
    ProofRegistry,
    bootstrap_receipt_bytes,
    build_bootstrap_receipt,
    observed_probe_result,
)


def _registry() -> ProofRegistry:
    return ProofRegistry(
        tuple(
            ConformanceProbe(
                proof_id,
                f"integration/{proof_id}",
                lambda _, proof_id=proof_id: observed_probe_result(
                    input_documents={"proof": proof_id},
                    output_documents={"actual_fake_provider_contract": proof_id},
                ),
            )
            for proof_id in REQUIRED_PROOF_IDS
        )
    )


def test_unpaid_conformance_uses_synthetic_catalog_without_network_or_provider_calls(
    tmp_path: Path, monkeypatch
) -> None:
    locks = dict.fromkeys(REQUIRED_STAGE_LOCKS, "a" * 64)
    lock_path = tmp_path / "stage-locks.json"
    lock_path.write_bytes(canonical_bytes(locks))
    output_root = tmp_path / "artifacts"
    catalog = Path(__file__).parents[2] / "catalog" / "catalog.yaml"
    bootstrap = bootstrap_receipt_bytes(
        build_bootstrap_receipt(
            mode="unpaid_ci",
            runtime_lock={"lock_sha256": locks["runtime_lock_sha256"]},
            input_hashes={"runtime": "a" * 64},
            output_hashes={"telemetry": "a" * 64},
            environment_sha256=locks["environment_sha256"],
            protocol_sha256=locks["protocol_sha256"],
        )
    )
    bootstrap_path = tmp_path / "bootstrap.json"
    bootstrap_path.write_bytes(bootstrap)
    calls: list[object] = []
    original_socket = socket.socket

    def tracked_socket(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return original_socket(*args, **kwargs)

    monkeypatch.setattr(socket, "socket", tracked_socket)

    assert (
        conformance(
            Namespace(
                mode="unpaid_ci",
                catalog=str(catalog),
                stage_locks=str(lock_path),
                output_root=str(output_root),
                bootstrap_receipt=str(bootstrap_path),
            ),
            proof_registry=_registry(),
        )
        == 0
    )

    reports = list((output_root / "conformance-reports").glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_bytes())
    assert report["mode"] == "unpaid_ci"
    assert report["status"] == "passed"
    assert {receipt["proof_id"] for receipt in report["proof_receipts"]} == REQUIRED_PROOF_IDS
    assert calls == []
