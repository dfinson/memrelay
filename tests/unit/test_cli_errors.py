"""Unit tests for the CLI's helpful-error and secret-redaction behavior (#15, SPEC §7).

Issue #15 [E10-S2] hardens the already-shipped command surface so it is safe to
operate without internals knowledge. Two behaviors are covered here:

* **Helpful errors** — a broken config (malformed TOML, a missing ``--path`` file, or a
  malformed ``[namespaces.*]`` section) must fail with a clear ``click`` error that names
  the file and how to recover, *not* a raw Python traceback. This is exercised through
  ``config`` and also through ``status`` / ``start`` to prove the shared ``_load_config``
  wrapper applies across the command surface.
* **Secret redaction** — ``memrelay config`` must never print a secret. The only real
  secret that can live in config is ``[graph.connection] password`` (the ``[llm]`` /
  ``[embeddings]`` blocks store an ``api_key_env`` *name*, not the key), so it is masked
  while every non-secret field stays visible.

Env is isolated per test (``cli_env`` pins the homes under ``tmp_path`` and clears
inherited ``MEMRELAY_*`` / ``XDG_*``) so a developer's real config can never leak in.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from memrelay.cli import main

MALFORMED_TOML = "this is = = not valid toml [[[\n"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ─── Helpful errors (AC4) ────────────────────────────────────────────────────


def test_config_malformed_toml_reports_clean_error(
    cli_env: tuple[Path, Path], tmp_path: Path
) -> None:
    bad = _write(tmp_path / "bad.toml", MALFORMED_TOML)
    result = CliRunner().invoke(main, ["config", "--path", str(bad)])

    assert result.exit_code != 0
    # A clean ClickException exits via SystemExit — never the raw parse error.
    assert isinstance(result.exception, SystemExit)
    assert not isinstance(result.exception, tomllib.TOMLDecodeError)
    assert "could not parse config file" in result.output
    assert str(bad) in result.output
    assert "memrelay init" in result.output  # points the user at recovery


def test_config_missing_path_reports_clean_error(
    cli_env: tuple[Path, Path], tmp_path: Path
) -> None:
    missing = tmp_path / "nope.toml"
    result = CliRunner().invoke(main, ["config", "--path", str(missing)])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert not isinstance(result.exception, FileNotFoundError)
    assert "config file not found" in result.output
    assert str(missing) in result.output


def test_config_malformed_namespaces_reports_clean_error(
    cli_env: tuple[Path, Path], tmp_path: Path
) -> None:
    bad = _write(tmp_path / "badns.toml", '[namespaces.foo]\nrepos = ["not-a-valid-repo"]\n')
    result = CliRunner().invoke(main, ["config", "--path", str(bad)])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "invalid configuration in" in result.output
    assert "foo" in result.output  # the offending namespace is named


def test_status_malformed_config_reports_clean_error(
    cli_env: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared wrapper hardens more than ``config`` — ``status`` must not traceback."""
    bad = _write(tmp_path / "bad.toml", MALFORMED_TOML)
    monkeypatch.setenv("MEMRELAY_CONFIG", str(bad))

    result = CliRunner().invoke(main, ["status"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "could not parse config file" in result.output


def test_start_malformed_config_reports_clean_error(
    cli_env: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``start`` fails at config-load with a clean error and never spawns a daemon."""
    bad = _write(tmp_path / "bad.toml", MALFORMED_TOML)
    monkeypatch.setenv("MEMRELAY_CONFIG", str(bad))

    result = CliRunner().invoke(main, ["start"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "could not parse config file" in result.output


# ─── Secret redaction (AC3) ──────────────────────────────────────────────────


CLOUD_CONFIG = (
    '[graph]\nbackend = "neo4j"\n'
    "[graph.connection]\n"
    'uri = "bolt://example:7687"\n'
    'user = "neo4j"\n'
    'password = "SUPER_SECRET_PW"\n'
)


def test_config_redacts_connection_password(cli_env: tuple[Path, Path], tmp_path: Path) -> None:
    cfg = _write(tmp_path / "cloud.toml", CLOUD_CONFIG)
    result = CliRunner().invoke(main, ["config", "--path", str(cfg)])

    assert result.exit_code == 0, result.output
    assert "SUPER_SECRET_PW" not in result.output  # the secret never appears
    data = json.loads(result.output)  # still valid JSON
    connection = data["graph"]["connection"]
    assert connection["password"] == "***redacted***"
    # Non-secret connection fields remain visible/useful.
    assert connection["uri"] == "bolt://example:7687"
    assert connection["user"] == "neo4j"


def test_config_keeps_api_key_env_name_visible(cli_env: tuple[Path, Path], tmp_path: Path) -> None:
    """``api_key_env`` is an env-var *name*, not a secret — it must stay visible."""
    cfg = _write(
        tmp_path / "byokey.toml",
        '[llm]\nstrategy = "byo-key"\nprovider = "openai"\napi_key_env = "OPENAI_API_KEY"\n',
    )
    result = CliRunner().invoke(main, ["config", "--path", str(cfg)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["llm"]["api_key_env"] == "OPENAI_API_KEY"
    assert "***redacted***" not in result.output


def test_config_default_output_is_valid_json_without_redaction(cli_env: tuple[Path, Path]) -> None:
    """Zero-config default: redaction is a no-op and output is unchanged valid JSON."""
    result = CliRunner().invoke(main, ["config"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["graph"]["connection"] is None
    assert "***redacted***" not in result.output
    assert "resolved_path" in data["graph"]
