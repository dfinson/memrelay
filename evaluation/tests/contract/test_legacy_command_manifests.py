from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

import pytest
from memrelay_eval.domain.errors import AnalysisError, StageControlError
from memrelay_eval.evidence.manifest import canonical_json_bytes

cli_main = importlib.import_module("memrelay_eval.cli.main")
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "command-manifest.schema.json"


def _manifest(root: Path, command: str) -> dict[str, object]:
    manifests = list((root / "commands").glob(f"{command}-*.json"))
    assert len(manifests) == 1
    return json.loads(manifests[0].read_text(encoding="utf-8"))


def _validate_standard_manifest(document: dict[str, object]) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(document, json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))
    assert canonical_json_bytes(document) == json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


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


def _generic_args(command: str, root: Path) -> argparse.Namespace:
    fields = {
        field
        for values in (
            *cli_main._INPUT_PATH_FIELDS.values(),
            *cli_main._OUTPUT_PATH_FIELDS.values(),
        )
        for field in values
    }
    values: dict[str, object] = dict.fromkeys(fields)
    values.update(
        command=command,
        command_manifest_root=str(root),
        stage="integration",
        output_root=None,
        artifacts_root=str(root),
        manifest=None,
        runtime_lock=None,
        protocol_sha256=None,
        dataset_version=None,
    )
    return argparse.Namespace(**values)


@pytest.mark.parametrize("command", sorted(cli_main._LEGACY_MANIFESTED_COMMANDS))
def test_every_generic_command_emits_one_failed_manifest_when_publish_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    def fail_publish(_path: Path, _data: bytes) -> None:
        raise StageControlError("command_manifest_publish_failed")

    monkeypatch.setattr(cli_main, "_write_immutable_manifest", fail_publish)

    with pytest.raises(StageControlError) as failure:
        cli_main._invoke_with_command_manifest(_generic_args(command, tmp_path), lambda _args: 0)

    assert failure.value.code == "command_manifest_publish_failed"
    printed = capsys.readouterr().out.splitlines()
    assert len(printed) == 1
    document = json.loads(printed[0])
    assert document["command"] == command
    assert document["terminal_status"] == "failed"
    assert document["error_code"] == "command_manifest_publish_failed"
    assert "prior_error_code" not in document
    _validate_standard_manifest(document)


def test_commands_path_collision_emits_failed_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "commands").write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(cli_main, "bootstrap", lambda _args: 0)
    monkeypatch.setattr(cli_main, "_lock_artifact_root", lambda: root)

    with pytest.raises(StageControlError) as failure:
        cli_main.main(
            [
                "bootstrap",
                "--backup-root",
                str(tmp_path / "backup"),
                "--command-manifest-root",
                str(root),
            ]
        )

    assert failure.value.code == "command_manifest_publish_failed"
    document = json.loads(capsys.readouterr().out.strip())
    assert document["terminal_status"] == "failed"
    assert document["error_code"] == "command_manifest_publish_failed"


def test_permission_failure_emits_failed_manifest_with_typed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def deny_link(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("injected")

    monkeypatch.setattr(cli_main.os, "link", deny_link)
    with pytest.raises(StageControlError) as failure:
        cli_main._invoke_with_command_manifest(_generic_args("report", tmp_path), lambda _args: 0)

    assert failure.value.code == "command_manifest_publish_failed"
    document = json.loads(capsys.readouterr().out.strip())
    assert document["terminal_status"] == "failed"
    assert document["error_code"] == "command_manifest_publish_failed"


def test_original_command_failure_is_retained_when_manifest_publish_also_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def deny_link(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("injected")

    def fail_command(_args: argparse.Namespace) -> int:
        raise AnalysisError("report_authority_invalid")

    monkeypatch.setattr(cli_main.os, "link", deny_link)
    with pytest.raises(StageControlError) as failure:
        cli_main._invoke_with_command_manifest(_generic_args("report", tmp_path), fail_command)

    assert failure.value.code == "command_manifest_publish_failed"
    assert failure.value.evidence[-1] == "report_authority_invalid"
    document = json.loads(capsys.readouterr().out.strip())
    assert document["terminal_status"] == "failed"
    assert document["error_code"] == "command_manifest_publish_failed"
    assert document["prior_error_code"] == "report_authority_invalid"
    _validate_standard_manifest(document)


def test_generic_manifest_publication_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _generic_args("report", tmp_path)

    assert cli_main._invoke_with_command_manifest(args, lambda _args: 0) == 0
    assert cli_main._invoke_with_command_manifest(args, lambda _args: 0) == 0

    document = _manifest(tmp_path, "report")
    assert document["terminal_status"] == "succeeded"
    assert capsys.readouterr().out == ""
