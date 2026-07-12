"""Regression: ``init`` must survive an ingest-only resolved provider (E12-S5 #71).

``memrelay init`` calls ``provider.register()`` unconditionally, and ``_resolve_provider(None)``
auto-detects the alphabetically-first *present* agent (no Copilot preference). After E12-S5 added
ingest-only providers (Codex, Continue, Aider, Antigravity, ...) that sort **before** ``copilot``
and raise ``NotImplementedError`` from ``register()``, a box with e.g. Codex installed would crash
``memrelay init`` with an unhandled error. ``init`` must instead skip MCP registration with an
honest line, still create the home + config, and exit 0.

Fully hermetic: the autouse embedding/FTS prefetch stubs (see ``conftest.py``) keep it offline,
and the provider is pointed at an inert ``tmp_path`` home, so nothing real is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from memrelay import cli
from memrelay.cli import main
from memrelay.providers.registry import get_registry


def test_init_skips_mcp_for_ingest_only_provider(
    cli_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A resolved ingest-only agent (Codex) does not crash init: MCP skipped, config lands."""
    home, _copilot = cli_env
    # A real ingest-only provider whose register() raises NotImplementedError, pointed at an
    # inert tmp home. Monkeypatch the resolver so the outcome is independent of what is installed.
    codex = get_registry().create("codex", home=str(tmp_path / "codex"))
    monkeypatch.setattr(cli, "_resolve_provider", lambda copilot_home: codex)

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0, result.output
    assert result.exception is None, result.output
    lowered = result.output.lower()
    assert "ingest-only" in lowered, result.output
    assert "skipped" in lowered, result.output
    assert "codex" in lowered, result.output
    # The happy-path registration line must be suppressed when nothing was registered.
    assert "registered mcp:" not in lowered, result.output
    # Setup still completes end-to-end even though MCP registration was unavailable.
    assert (home / "config.toml").is_file(), "config must still be written"
