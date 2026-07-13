"""Unit tests for the CLI's helpful-error and secret-redaction behavior (#15, SPEC §7).

Issue #15 [E10-S2] hardens the already-shipped command surface so it is safe to
operate without internals knowledge. Two behaviors are covered here:

* **Helpful errors** — a broken config (malformed TOML, a missing ``--path`` file, or a
  malformed ``[namespaces.*]`` section) must fail with a clear ``click`` error that names
  the file and how to recover, *not* a raw Python traceback. This is exercised through
  ``config`` and also through ``status`` / ``start`` to prove the shared ``_load_config``
  wrapper applies across the command surface.
* **Secret redaction** — ``memrelay config`` must never print a secret. The secrets that
  can live in config are ``[graph.connection] password`` and a password embedded inline in
  ``[graph.connection] uri`` (``scheme://user:pass@host``); both are masked. The ``[llm]`` /
  ``[embeddings]`` blocks store an ``api_key_env`` *name*, not the key, so — like ``host`` /
  ``user`` / a credential-free ``uri`` — they stay visible.

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


# The shared ``_load_config`` wrapper must behave identically across *every* command that
# loads config — a clean ``ClickException`` (surfacing as ``SystemExit``), never a raw
# traceback. This includes the agent-facing ``mcp`` stdio server and the internal ``_serve``
# daemon entrypoint (#153, F3), which previously called ``load_config`` directly. These
# commands reach config-loading with no required arguments, so a malformed ``MEMRELAY_CONFIG``
# drives each straight through the wrapper.
WRAPPED_COMMANDS = ["status", "start", "stop", "observe", "mcp", "_serve"]


@pytest.mark.parametrize("command", WRAPPED_COMMANDS)
def test_wrapped_command_reports_clean_error_on_malformed_config(
    command: str, cli_env: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consistency lock: no wrapped command leaks a raw traceback on a broken config."""
    bad = _write(tmp_path / "bad.toml", MALFORMED_TOML)
    monkeypatch.setenv("MEMRELAY_CONFIG", str(bad))

    result = CliRunner().invoke(main, [command])

    assert result.exit_code != 0
    # A clean ClickException exits via SystemExit — never the raw parse error.
    assert isinstance(result.exception, SystemExit)
    assert not isinstance(result.exception, tomllib.TOMLDecodeError)
    assert "could not parse config file" in result.output
    assert str(bad) in result.output


def test_config_unresolved_env_var_reports_clean_error(
    cli_env: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2 (#153): a config path referencing an *unset* env var must fail loud with a clean
    error that names the variable — never silently resolve under the current directory."""
    monkeypatch.delenv("RT153_UNSET_DIR", raising=False)
    bad = _write(tmp_path / "unsetvar.toml", '[graph]\npath = "${RT153_UNSET_DIR}/graph.db"\n')

    result = CliRunner().invoke(main, ["config", "--path", str(bad)])

    assert result.exit_code != 0
    # A clean ClickException exits via SystemExit — not a raw ValueError/ConfigError traceback.
    assert isinstance(result.exception, SystemExit)
    assert "invalid configuration in" in result.output
    assert "RT153_UNSET_DIR" in result.output  # the offending variable is named
    assert str(bad) in result.output


def test_init_malformed_config_error_is_non_circular(
    cli_env: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``init`` must not tell a user already running ``init`` to run ``memrelay init``."""
    bad = _write(tmp_path / "bad.toml", MALFORMED_TOML)
    monkeypatch.setenv("MEMRELAY_CONFIG", str(bad))

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "could not parse config file" in result.output
    # Command-appropriate, non-circular recovery: fix/delete and re-run, not "memrelay init".
    assert "memrelay init" not in result.output
    assert "re-run" in result.output


def test_load_config_success_path_returns_unchanged_config(
    cli_env: tuple[Path, Path], tmp_path: Path
) -> None:
    """Backward-compat: on a valid config the wrapper is a transparent pass-through."""
    from memrelay.cli import _load_config
    from memrelay.config import Config, load_config

    # No-arg seam (init/start/stop/status/observe/forget all use this).
    wrapped = _load_config()
    direct = load_config()
    assert isinstance(wrapped, Config)
    assert wrapped == direct
    assert wrapped.to_dict() == direct.to_dict()

    # Explicit --path seam (used by ``config --path``).
    ok = _write(tmp_path / "ok.toml", '[graph]\nbackend = "ladybug"\n')
    assert _load_config(path=str(ok)) == load_config(path=str(ok))
    assert _load_config(path=str(ok)).to_dict() == load_config(path=str(ok)).to_dict()


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


CLOUD_CONFIG_WITH_URI_CREDS = (
    '[graph]\nbackend = "neo4j"\n'
    "[graph.connection]\n"
    'uri = "neo4j+s://neo4j:URI_SECRET_PW@db.example.com:7687"\n'
    'user = "neo4j"\n'
    'password = "FIELD_SECRET_PW"\n'
    'database = "neo4j"\n'
)


def test_config_redacts_every_secret_value(cli_env: tuple[Path, Path], tmp_path: Path) -> None:
    """Redaction is complete: no secret value survives, in either the field or the URI."""
    cfg = _write(tmp_path / "creds.toml", CLOUD_CONFIG_WITH_URI_CREDS)
    result = CliRunner().invoke(main, ["config", "--path", str(cfg)])

    assert result.exit_code == 0, result.output
    # Neither secret value appears anywhere in the rendered output.
    assert "FIELD_SECRET_PW" not in result.output
    assert "URI_SECRET_PW" not in result.output

    data = json.loads(result.output)  # output stays valid JSON
    connection = data["graph"]["connection"]
    assert connection["password"] == "***redacted***"
    # URI: only the embedded password is masked; scheme/user/host/port stay intact.
    assert connection["uri"] == "neo4j+s://neo4j:***redacted***@db.example.com:7687"
    # Non-secret references are preserved.
    assert connection["user"] == "neo4j"
    assert connection["database"] == "neo4j"


#: Sentinel password token used in URI edge-case fixtures; kept out of a literal
#: ``user:pass@host`` sequence (via f-string interpolation) so it reads clearly.
_URI_PW = "URI_PW_SENTINEL"


@pytest.mark.parametrize(
    ("uri", "expect_redacted"),
    [
        # scheme://user:pass@host — normal case: mask password, keep user + endpoint.
        (f"bolt://neo4j:{_URI_PW}@db.example.com:7687", True),
        # userinfo but NO password (bolt://user@host) — nothing to redact, untouched.
        ("bolt://neo4j@db.example.com:7687", False),
        # password but NO user — masked without crashing or mangling the endpoint.
        (f"bolt://:{_URI_PW}@db.example.com:7687", True),
        # '@' inside the password — last '@' delimits host; password still fully masked.
        (f"bolt://neo4j:{_URI_PW}x@yz@db.example.com:7687", True),
        # no userinfo at all — untouched.
        ("bolt://db.example.com:7687", False),
        # non-URL junk / empty — returned unchanged, never raises.
        ("not a uri", False),
        ("", False),
    ],
)
def test_redact_uri_credentials_edge_cases(uri: str, expect_redacted: bool) -> None:
    """The URI mask must handle every userinfo shape without leaking or mangling."""
    from memrelay.cli import _redact_uri_credentials

    out = _redact_uri_credentials(uri)

    assert _URI_PW not in out  # the embedded password never survives, in any shape
    if expect_redacted:
        assert "***redacted***" in out
        # The endpoint (host:port after the userinfo) is preserved byte-for-byte.
        assert out.endswith("@db.example.com:7687")
    else:
        assert out == uri  # nothing to redact -> transparent pass-through


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
