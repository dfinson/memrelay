"""CLI behavior tests for ``memrelay guidance`` (E10-S3, issue #16).

Focus on the opt-in contract from the command's point of view: ``--dry-run`` and a
declined confirmation write nothing; an explicit confirmation (or ``--yes``) writes the
marked block; re-runs are byte-identical; ``--target`` / ``--path`` hit the right files;
existing content is preserved and malformed markers are refused without mutation.

Hermetic: ``CliRunner.isolated_filesystem`` / ``tmp_path``. The command loads no config
and touches no home dir, so nothing else needs isolating.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from memrelay.cli import main

TOOL_NAMES = ("memory_recall", "memory_detail", "memory_note")
MARKER_START = "<!-- memrelay:guidance:start -->"


def test_guidance_listed_in_help() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "guidance" in result.output


def test_dry_run_previews_but_writes_nothing() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["guidance", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert MARKER_START in result.output
        for tool in TOOL_NAMES:
            assert tool in result.output
        assert "nothing written" in result.output
        assert not Path("AGENTS.md").exists()


def test_yes_writes_agents_md_by_default() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["guidance", "--yes"])
        assert result.exit_code == 0, result.output
        content = Path("AGENTS.md").read_text(encoding="utf-8")
        assert MARKER_START in content
        for tool in TOOL_NAMES:
            assert tool in content


def test_confirmation_declined_writes_nothing() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["guidance"], input="n\n")
        assert result.exit_code == 0, result.output
        assert "nothing written" in result.output
        assert not Path("AGENTS.md").exists()


def test_confirmation_accepted_writes_file() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["guidance"], input="y\n")
        assert result.exit_code == 0, result.output
        assert Path("AGENTS.md").is_file()


def test_rerun_is_idempotent_and_unchanged() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        first = runner.invoke(main, ["guidance", "--yes"])
        assert first.exit_code == 0, first.output
        before = Path("AGENTS.md").read_bytes()

        second = runner.invoke(main, ["guidance", "--yes"])
        assert second.exit_code == 0, second.output
        assert "already up to date" in second.output
        assert Path("AGENTS.md").read_bytes() == before


def test_append_preserves_existing_file_content() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        original = "# House rules\n\nAlways run the tests.\n"
        Path("AGENTS.md").write_text(original, encoding="utf-8")

        result = runner.invoke(main, ["guidance", "--yes"])
        assert result.exit_code == 0, result.output
        content = Path("AGENTS.md").read_text(encoding="utf-8")
        assert content.startswith(original)
        assert MARKER_START in content


def test_target_claude_writes_claude_md() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["guidance", "--target", "claude", "--yes"])
        assert result.exit_code == 0, result.output
        assert Path("CLAUDE.md").is_file()
        assert not Path("AGENTS.md").exists()


def test_target_copilot_creates_github_instructions() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["guidance", "--target", "copilot", "--yes"])
        assert result.exit_code == 0, result.output
        assert Path(".github/copilot-instructions.md").is_file()


def test_explicit_path_overrides_target(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "INSTRUCTIONS.md"
    result = CliRunner().invoke(main, ["guidance", "--path", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert target.is_file()
    assert MARKER_START in target.read_text(encoding="utf-8")


def test_malformed_markers_are_refused_without_mutation() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        original = f"# Notes\n\n{MARKER_START}\ndangling start, no end\n"
        Path("AGENTS.md").write_text(original, encoding="utf-8")

        result = runner.invoke(main, ["guidance", "--yes"])
        assert result.exit_code != 0
        assert "marker" in result.output.lower()
        # The file was not touched.
        assert Path("AGENTS.md").read_text(encoding="utf-8") == original
