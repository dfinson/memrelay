"""CLI command manifest tests for plan-offline.

Validates AC-2: non-interactive, typed terminal status, exit codes, manifest completeness.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from memrelay_eval.cli.main import main

CATALOG_PATH = Path(__file__).parents[2] / "catalog" / "catalog.yaml"


@pytest.fixture(autouse=True)
def isolated_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run planning against a copied catalog so tests never alter repository inputs."""
    source_catalog = CATALOG_PATH
    catalog_root = tmp_path / "catalog"
    shutil.copytree(source_catalog.parent, catalog_root)
    monkeypatch.setitem(globals(), "CATALOG_PATH", catalog_root / "catalog.yaml")


def planning_output_dir() -> Path:
    output_dir = CATALOG_PATH.parent / "generated"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


class TestPlanOfflineCLI:
    """Test the plan-offline CLI command end-to-end."""

    def test_help_does_not_prompt(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["plan-offline", "--help"])
        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "plan-offline" in output

    def test_successful_exit_code_zero(self, tmp_path: Path) -> None:
        output_dir = planning_output_dir()
        manifest = tmp_path / "manifest.json"
        exit_code = main(
            [
                "plan-offline",
                "--catalog",
                str(CATALOG_PATH),
                "--output-dir",
                str(output_dir),
                "--manifest",
                str(manifest),
            ]
        )
        assert exit_code == 0
        document = json.loads(manifest.read_text())
        assert document["terminal_status"] == "succeeded"

    def test_stdout_is_valid_command_manifest(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output_dir = planning_output_dir()
        manifest = tmp_path / "manifest.json"
        main(
            [
                "plan-offline",
                "--catalog",
                str(CATALOG_PATH),
                "--output-dir",
                str(output_dir),
                "--manifest",
                str(manifest),
            ]
        )
        output = capsys.readouterr().out
        document = json.loads(output)
        assert document["command"] == "plan-offline"
        assert document["terminal_status"] == "succeeded"
        assert document["exit_code"] == 0
        assert "input_hashes" in document
        assert "output_hashes" in document
        assert "digest" in document
        assert json.loads(manifest.read_text()) == document

    def test_invalid_catalog_returns_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad_catalog = tmp_path / "bad.yaml"
        bad_catalog.write_text("not: valid: catalog: syntax: [")
        output_dir = planning_output_dir()
        manifest = tmp_path / "manifest.json"
        exit_code = main(
            [
                "plan-offline",
                "--catalog",
                str(bad_catalog),
                "--output-dir",
                str(output_dir),
                "--manifest",
                str(manifest),
            ]
        )
        assert exit_code != 0
        output = capsys.readouterr().out
        document = json.loads(output)
        assert document["terminal_status"] == "failed"

    def test_nonexistent_catalog_returns_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output_dir = planning_output_dir()
        manifest = tmp_path / "manifest.json"
        exit_code = main(
            [
                "plan-offline",
                "--catalog",
                str(tmp_path / "nonexistent.yaml"),
                "--output-dir",
                str(output_dir),
                "--manifest",
                str(manifest),
            ]
        )
        assert exit_code != 0

    def test_never_prompts_for_input(self, tmp_path: Path) -> None:
        """Verify the command never calls input() or reads stdin interactively."""
        output_dir = planning_output_dir()
        manifest = tmp_path / "manifest.json"
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            exit_code = main(
                [
                    "plan-offline",
                    "--catalog",
                    str(CATALOG_PATH),
                    "--output-dir",
                    str(output_dir),
                    "--manifest",
                    str(manifest),
                ]
            )
        assert exit_code == 0
