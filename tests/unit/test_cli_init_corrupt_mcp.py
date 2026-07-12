"""Regression: ``init`` must fail cleanly on a corrupt existing MCP config (sibling of #71).

The E12-S5 #71 fix made ``init`` survive an *ingest-only* resolved provider by catching the
``NotImplementedError`` its ``register()`` raises. But that ``try`` caught **only**
``NotImplementedError`` — and all five *registerable* providers (copilot, claude, cline, amazonq,
opencode) raise ``ValueError`` from ``register()`` when the agent's MCP config file is present but
not valid JSON, refusing to clobber a user's hand-edited file. That ``ValueError`` escaped
uncaught, so ``memrelay init`` crashed with a raw Python traceback and a non-zero exit whenever a
user had a one-character typo in e.g. ``~/.copilot/mcp-config.json``. ``init`` must instead surface
a clean, actionable ``click.ClickException`` that names the offending file.

The per-provider ``providers/*`` "not valid JSON" tests call ``register()`` directly and never
exercise the ``init`` CLI, so this crash was untested end-to-end; this closes that gap by driving
the failure through the CLI. Fully hermetic: ``cli_env`` pins the memrelay + Copilot homes under
``tmp_path`` and clears inherited overrides, and the autouse prefetch stubs keep it offline (the
error is raised before any prefetch runs regardless).
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from memrelay.cli import main
from memrelay.providers.copilot import MCP_CONFIG_FILENAME

#: A realistic hand-edit typo (stray comma → unterminated object): not valid JSON.
_CORRUPT_JSON = '{"mcpServers": {,}\n'


def test_init_reports_clean_error_on_corrupt_mcp_config(cli_env: tuple[Path, Path]) -> None:
    """A corrupt existing ``mcp-config.json`` yields a clean ClickException, not a crash."""
    home, copilot = cli_env
    copilot.mkdir(parents=True, exist_ok=True)
    mcp_config = copilot / MCP_CONFIG_FILENAME
    mcp_config.write_text(_CORRUPT_JSON, encoding="utf-8")

    # ``--copilot-home`` forces the (registerable) Copilot provider deterministically, independent
    # of whatever agent happens to be "installed" on the box running the suite.
    result = CliRunner().invoke(main, ["init", "--copilot-home", str(copilot)])

    # Clean, actionable failure rather than an uncaught crash.
    assert result.exit_code != 0, result.output
    # The provider's ``ValueError`` must NOT propagate uncaught (that is the bug). A fixed ``init``
    # converts it to a ``click.ClickException``, which CliRunner surfaces as ``SystemExit`` — never
    # a bare ``ValueError``, and never with an empty (raw-traceback) output.
    assert not isinstance(result.exception, ValueError), repr(result.exception)
    lowered = result.output.lower()
    assert "mcp registration failed" in lowered, result.output
    assert "not valid json" in lowered, result.output
    # The message must name the offending file so the user knows exactly what to fix.
    assert MCP_CONFIG_FILENAME in result.output, result.output
    # Refusing to clobber is the whole point: the corrupt file is left byte-for-byte untouched.
    assert mcp_config.read_text(encoding="utf-8") == _CORRUPT_JSON
    # ``init`` writes memrelay's own config before attempting MCP registration, so it still lands
    # even though registration failed — the error tells the user how to finish the last step.
    assert (home / "config.toml").is_file(), "memrelay config must still be written"
