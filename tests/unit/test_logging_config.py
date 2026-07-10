"""Unit tests for :mod:`memrelay.logging_config` (E11-S6, #22).

Covers the structured-logging configuration and -- most importantly -- AC4
"no secrets logged": a password embedded in a connection URI, or bound under a
secret-named key, must never reach the emitted log stream, whether it originates from an
existing stdlib ``logging.getLogger`` call site or from a native structlog logger.

Self-contained by design: a local ``autouse`` fixture snapshots and restores the root
logger's handlers/level and resets structlog's global state, so configuring logging here
never leaks into the other tests in the suite. Every assertion reads an injected
``StringIO`` (never ``caplog``/``capsys``), so the checks are deterministic and independent
of pytest's logging plugin (which is disabled under ``PYTEST_DISABLE_PLUGIN_AUTOLOAD``).
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import pytest
import structlog
from click.testing import CliRunner

from memrelay import cli
from memrelay.cli import main
from memrelay.config import Config, LoggingConfig, load_config
from memrelay.logging_config import (
    _REDACTED,
    configure_logging,
    redact_processor,
)


@pytest.fixture(autouse=True)
def _restore_logging_state() -> Any:
    """Snapshot/restore global logging state so these tests never leak (600-test safety)."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        structlog.reset_defaults()


def _redact(event_dict: dict[str, Any]) -> dict[str, Any]:
    """Run :func:`redact_processor` in isolation (no handler/formatter)."""
    return redact_processor(None, "info", event_dict)


# ─── redact_processor (AC4, unit level) ──────────────────────────────────────────


def test_redacts_secret_named_keys() -> None:
    out = _redact(
        {
            "password": "hunter2",
            "token": "t-abc",
            "api_key": "k-xyz",
            "authorization": "Bearer zzz",
            "credential": "c-1",
            "user": "alice",
        }
    )
    for key in ("password", "token", "api_key", "authorization", "credential"):
        assert out[key] == _REDACTED, key
    assert out["user"] == "alice"  # non-secret key preserved verbatim


def test_redacts_uri_userinfo_in_message_and_values() -> None:
    out = _redact(
        {
            "event": "connecting to neo4j://neo4j:s3cr3t@graph.local:7687 now",
            "dsn": "bolt://svc:p@ssw0rd@db:7687",  # '@' inside the password
            "url": "https://example.com/download",  # credential-free URL, untouched
        }
    )
    # message: password gone, scheme/user/host preserved
    assert "s3cr3t" not in out["event"]
    assert _REDACTED in out["event"]
    assert "neo4j://neo4j:" in out["event"]
    assert "@graph.local:7687 now" in out["event"]
    # '@'-in-password shape fully masked (host is delimited by the *last* '@')
    assert "p@ssw0rd" not in out["dsn"]
    assert out["dsn"] == f"bolt://svc:{_REDACTED}@db:7687"
    # a URL with no inline credentials is left byte-for-byte intact
    assert out["url"] == "https://example.com/download"


def test_redacts_recurses_into_nested_dict_and_list() -> None:
    out = _redact(
        {
            "graph": {"connection": {"password": "pw", "uri": "neo4j://u:pw@h:7687"}},
            "hosts": ["neo4j://a:sekret@h1:7687", "plain-host"],
        }
    )
    conn = out["graph"]["connection"]
    assert conn["password"] == _REDACTED
    assert "pw" not in conn["uri"] and _REDACTED in conn["uri"]
    assert "sekret" not in out["hosts"][0] and _REDACTED in out["hosts"][0]
    assert out["hosts"][1] == "plain-host"


def test_leaves_plain_message_and_nonsecret_keys_untouched() -> None:
    event = {"event": "ingested 3 records", "user": "alice", "host": "db.local", "count": 3}
    out = _redact(dict(event))
    assert out == event  # nothing to redact -> unchanged (incl. whitespace)


# ─── configure_logging: rendering, redaction end-to-end, levels ──────────────────


def test_emits_single_json_line_with_expected_fields() -> None:
    buf = io.StringIO()
    configure_logging("INFO", stream=buf)
    logging.getLogger("memrelay.sample").info("hello")
    lines = buf.getvalue().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "hello"
    assert record["level"] == "info"
    assert record["logger"] == "memrelay.sample"
    assert "timestamp" in record


def test_no_secrets_in_emitted_output_stdlib_and_structlog() -> None:
    """AC4 headline: neither the existing stdlib path nor structlog leaks a secret."""
    buf = io.StringIO()
    configure_logging("INFO", stream=buf)

    # (a) an *existing* stdlib call site style: %-arg carrying a credentialled URI
    logging.getLogger("memrelay.ingest.demo").warning(
        "connect %s", "neo4j://user:PWSECRET@host:7687"
    )
    # (b) native structlog with secrets bound as structured fields
    structlog.get_logger("memrelay.mcp.demo").info(
        "auth", password="hunter2", token="t-abc", user="alice"
    )

    out = buf.getvalue()
    # nothing secret survives, in any form
    assert "PWSECRET" not in out
    assert "hunter2" not in out
    assert "t-abc" not in out
    # redaction marker present and non-secret context retained for diagnosis
    assert _REDACTED in out
    assert "alice" in out
    assert "neo4j://user:" in out and "@host:7687" in out
    # every emitted line is still valid JSON
    for line in out.strip().splitlines():
        json.loads(line)


def test_level_filtering_respects_configured_level() -> None:
    buf = io.StringIO()
    configure_logging("WARNING", stream=buf)
    log = logging.getLogger("memrelay.level")
    log.info("dropped")
    log.warning("kept")
    out = buf.getvalue()
    assert "dropped" not in out
    assert "kept" in out


def test_bad_level_falls_back_to_info_without_raising() -> None:
    buf = io.StringIO()
    configure_logging("NOT-A-LEVEL", stream=buf)  # must not raise (zero-config safety)
    assert logging.getLogger().level == logging.INFO
    logging.getLogger("memrelay.fallback").info("shown")
    assert "shown" in buf.getvalue()


# ─── config knob (AC2): default / env / TOML ─────────────────────────────────────


def test_logging_level_default_is_info() -> None:
    assert Config().logging.level == "INFO"


def test_logging_level_env_override() -> None:
    cfg = load_config(environ={"MEMRELAY_LOGGING__LEVEL": "debug"})
    assert cfg.logging.level == "debug"


def test_logging_level_toml_override(tmp_path: Any) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('[logging]\nlevel = "DEBUG"\n', encoding="utf-8")
    cfg = load_config(path=str(config_file), environ={})
    assert cfg.logging.level == "DEBUG"


def test_logging_block_absent_keeps_default(tmp_path: Any) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('home = "~/somewhere"\n', encoding="utf-8")
    cfg = load_config(path=str(config_file), environ={})
    assert cfg.logging.level == "INFO"


# ─── entrypoint wiring (AC1): daemon + MCP both configure logging ────────────────


def test_serve_configures_logging_and_threads_config(monkeypatch: Any) -> None:
    import memrelay.daemon.lifecycle as lifecycle

    cfg = Config(logging=LoggingConfig(level="DEBUG"))
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "configure_logging", lambda level: seen.__setitem__("level", level))
    monkeypatch.setattr(lifecycle, "run_foreground", lambda c: seen.__setitem__("cfg", c))

    result = CliRunner().invoke(main, ["_serve"])

    assert result.exit_code == 0, result.output
    assert seen["level"] == "DEBUG"
    assert seen["cfg"] is cfg


def test_mcp_configures_logging_and_threads_config(monkeypatch: Any) -> None:
    import memrelay.mcp.server as server

    cfg = Config(logging=LoggingConfig(level="WARNING"))
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "configure_logging", lambda level: seen.__setitem__("level", level))
    monkeypatch.setattr(server, "run_stdio", lambda c=None: seen.__setitem__("cfg", c))

    result = CliRunner().invoke(main, ["mcp"])

    assert result.exit_code == 0, result.output
    assert seen["level"] == "WARNING"
    assert seen["cfg"] is cfg
