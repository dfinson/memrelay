"""Unpaid catalog-to-report conformance integration coverage."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.cli.main import main
from memrelay_eval.evidence.conformance import REQUIRED_PROOF_IDS, REQUIRED_STAGE_LOCKS


def test_unpaid_conformance_uses_synthetic_catalog_without_network_or_provider_calls(
    tmp_path: Path, monkeypatch
) -> None:
    locks = dict.fromkeys(REQUIRED_STAGE_LOCKS, "a" * 64)
    lock_path = tmp_path / "stage-locks.json"
    lock_path.write_bytes(canonical_bytes(locks))
    output_root = tmp_path / "artifacts"
    catalog = Path(__file__).parents[2] / "catalog" / "catalog.yaml"
    calls: list[object] = []
    original_socket = socket.socket

    def tracked_socket(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return original_socket(*args, **kwargs)

    monkeypatch.setattr(socket, "socket", tracked_socket)

    assert (
        main(
            [
                "conformance",
                "--catalog",
                str(catalog),
                "--stage-locks",
                str(lock_path),
                "--output-root",
                str(output_root),
            ]
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
