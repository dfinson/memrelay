"""Unit tests for the memrelay CLI surface (SPEC §7)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from memrelay import __version__
from memrelay.cli import main


def test_help_lists_all_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in (
        "init",
        "start",
        "stop",
        "status",
        "guidance",
        "forget",
        "seed",
        "config",
        "mcp",
    ):
        assert command in result.output


def test_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_command_emits_json_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The CLI reads the real process environment, so isolate it: clear any
    # MEMRELAY_* overrides and point HOME/USERPROFILE/XDG at an empty tmp dir, so
    # the command reports built-in defaults rather than a developer's real config.
    for key in list(os.environ):
        if key.startswith("MEMRELAY_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    result = CliRunner().invoke(main, ["config"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["graph"]["backend"] == "ladybug"
    assert data["llm"]["strategy"] == "borrow-host"
    assert "resolved_path" in data["graph"]


def test_forget_requires_a_target() -> None:
    result = CliRunner().invoke(main, ["forget"])
    assert result.exit_code != 0
    assert "repo" in result.output.lower() or "namespace" in result.output.lower()


def test_seed_reports_git_error_on_non_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `seed` is implemented (E9-S4): pointed at a non-git directory it must fail with a
    # clean git error, not a stub message or a traceback. Env is isolated so the command
    # never reads or writes a developer's real ~/.memrelay.
    for key in list(os.environ):
        if key.startswith("MEMRELAY_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    result = CliRunner().invoke(main, ["seed", "--path", str(tmp_path), "--dry-run"])

    assert result.exit_code != 0
    assert "not implemented" not in result.output
    assert "git" in result.output.lower()
