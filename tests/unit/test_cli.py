"""Unit tests for the memrelay CLI surface (SPEC §7)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from memrelay import __version__
from memrelay.cli import main


def test_help_lists_all_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "start", "stop", "status", "forget", "seed", "config", "mcp"):
        assert command in result.output


def test_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_command_emits_json_defaults() -> None:
    result = CliRunner().invoke(main, ["config"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["graph"]["backend"] == "kuzu"
    assert data["llm"]["strategy"] == "borrow-host"
    assert "resolved_path" in data["graph"]


def test_forget_requires_a_target() -> None:
    result = CliRunner().invoke(main, ["forget"])
    assert result.exit_code != 0
    assert "repo" in result.output.lower() or "namespace" in result.output.lower()


def test_stub_command_exits_cleanly() -> None:
    result = CliRunner().invoke(main, ["status"])
    assert result.exit_code == 0
    assert "not implemented yet" in result.output
