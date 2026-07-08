"""Unit tests for the daemon-facing CLI commands (E6-S1, E7-S6).

``init`` registration is checked for idempotency + merge; ``start``/``status``/
``stop`` are exercised against an in-process daemon (``fake_daemon_spawn``) so no
real subprocess is launched. Everything is pinned under ``tmp_path`` by
``cli_env`` — the real ``~/.copilot`` / ``~/.memrelay`` are never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from memrelay.cli import main


def test_init_creates_home_config_and_registers(cli_env: tuple[Path, Path]) -> None:
    home, copilot = cli_env
    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output

    assert (home / "config.toml").is_file()
    entry = json.loads((copilot / "mcp-config.json").read_text(encoding="utf-8"))
    server = entry["mcpServers"]["memrelay"]
    assert server["type"] == "local"  # de-risk delta: 'local', not SPEC's 'stdio'
    assert server["command"] == "memrelay"
    assert server["args"] == ["mcp"]


def test_init_renders_llm_block_from_provider_hint(cli_env: tuple[Path, Path]) -> None:
    """The generated ``[llm]`` block comes from the resolved provider's strategy hint."""
    home, _ = cli_env
    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output

    config_text = (home / "config.toml").read_text(encoding="utf-8")
    assert 'strategy = "borrow-host"' in config_text
    assert 'host = "copilot"' in config_text


def test_init_routes_provider_through_registry(cli_env: tuple[Path, Path], monkeypatch) -> None:
    """``init`` resolves its provider via ``_resolve_provider`` (the registry seam)."""
    from memrelay import cli

    calls: list = []
    real_resolve = cli._resolve_provider

    def spy_resolve(copilot_home):
        calls.append(copilot_home)
        return real_resolve(copilot_home)

    monkeypatch.setattr(cli, "_resolve_provider", spy_resolve)
    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1  # provider resolved exactly once, through the seam


def test_init_is_idempotent(cli_env: tuple[Path, Path]) -> None:
    _, copilot = cli_env
    runner = CliRunner()
    assert runner.invoke(main, ["init"]).exit_code == 0
    first = (copilot / "mcp-config.json").read_text(encoding="utf-8")
    assert runner.invoke(main, ["init"]).exit_code == 0
    second = (copilot / "mcp-config.json").read_text(encoding="utf-8")
    assert first == second


def test_init_merges_with_existing_servers(cli_env: tuple[Path, Path]) -> None:
    _, copilot = cli_env
    copilot.mkdir(parents=True)
    (copilot / "mcp-config.json").write_text(
        json.dumps({"mcpServers": {"github": {"type": "local", "command": "gh"}}}),
        encoding="utf-8",
    )
    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0

    servers = json.loads((copilot / "mcp-config.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"github", "memrelay"}
    assert servers["github"]["command"] == "gh"  # untouched


def test_init_keeps_existing_config(cli_env: tuple[Path, Path]) -> None:
    home, _ = cli_env
    home.mkdir(parents=True)
    (home / "config.toml").write_text("# hand-edited\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0
    assert (home / "config.toml").read_text(encoding="utf-8") == "# hand-edited\n"
    assert "kept existing" in result.output


def test_status_reports_not_running(cli_env: tuple[Path, Path]) -> None:
    result = CliRunner().invoke(main, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.output


def test_start_status_stop_cycle(cli_env: tuple[Path, Path], fake_daemon_spawn: dict) -> None:
    runner = CliRunner()

    started = runner.invoke(main, ["start"])
    assert started.exit_code == 0, started.output
    assert "started" in started.output
    assert fake_daemon_spawn["count"] == 1

    status = runner.invoke(main, ["status"])
    assert status.exit_code == 0
    assert "running" in status.output
    assert "sessions_observed" in status.output

    stopped = runner.invoke(main, ["stop"])
    assert stopped.exit_code == 0
    assert "stopped" in stopped.output

    after = runner.invoke(main, ["status"])
    assert "not running" in after.output


def test_double_start_does_not_respawn(cli_env: tuple[Path, Path], fake_daemon_spawn: dict) -> None:
    runner = CliRunner()
    assert runner.invoke(main, ["start"]).exit_code == 0
    second = runner.invoke(main, ["start"])
    assert second.exit_code == 0
    assert "already running" in second.output
    assert fake_daemon_spawn["count"] == 1  # single-instance: no second spawn
    runner.invoke(main, ["stop"])
