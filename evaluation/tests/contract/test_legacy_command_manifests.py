from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from memrelay_eval.domain.errors import AnalysisError

cli_main = importlib.import_module("memrelay_eval.cli.main")


def _manifest(root: Path, command: str) -> dict[str, object]:
    manifests = list((root / "commands").glob(f"{command}-*.json"))
    assert len(manifests) == 1
    return json.loads(manifests[0].read_text(encoding="utf-8"))


def test_bootstrap_cli_appends_standard_immutable_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "runtime-lock.json").write_text("runtime authority", encoding="utf-8")

    monkeypatch.setattr(cli_main, "bootstrap", lambda _args: 0)
    monkeypatch.setattr(cli_main, "_lock_artifact_root", lambda: root)

    assert (
        cli_main.main(
            [
                "bootstrap",
                "--backup-root",
                str(tmp_path / "backup"),
                "--command-manifest-root",
                str(root),
            ]
        )
        == 0
    )

    document = _manifest(root, "bootstrap")
    assert document["terminal_status"] == "succeeded"
    assert document["exit_code"] == 0
    assert document["error_code"] is None
    assert document["output_hashes"]
    assert document["runtime_lock_sha256"]


def test_reconcile_cli_binds_default_authority_input_and_locks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "artifacts"
    input_path = root / "reconciliation" / "integration.input.json"
    input_path.parent.mkdir(parents=True)
    input_path.write_text(
        json.dumps(
            {
                "protocol_sha256": "a" * 64,
                "runtime_lock_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_main, "reconcile_stage", lambda _args: 0)

    assert (
        cli_main.main(["reconcile", "--stage", "integration", "--artifacts-root", str(root)]) == 0
    )

    document = _manifest(root, "reconcile")
    assert document["input_hashes"]["input"]
    assert document["runtime_lock_sha256"] == "b" * 64
    assert document["protocol_sha256"] == "a" * 64


def test_report_cli_appends_typed_failure_manifest_before_reraising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stage_evidence = tmp_path / "stage-evidence.json"
    stage_evidence.write_text("{}", encoding="utf-8")
    parquet_root = tmp_path / "parquet"
    parquet_root.mkdir()
    output_root = tmp_path / "output"

    def fail(_args: object) -> int:
        raise AnalysisError("report_authority_invalid")

    monkeypatch.setattr(cli_main, "report_stage", fail)

    with pytest.raises(AnalysisError, match="report authority invalid"):
        cli_main.main(
            [
                "report",
                "--stage",
                "integration",
                "--stage-evidence",
                str(stage_evidence),
                "--parquet-root",
                str(parquet_root),
                "--dataset-version",
                "dataset-v1",
                "--output-root",
                str(output_root),
            ]
        )

    document = _manifest(output_root, "report")
    assert document["terminal_status"] == "failed"
    assert document["exit_code"] == 2
    assert document["error_code"] == "report_authority_invalid"
    assert document["input_hashes"]["stage_evidence"]
