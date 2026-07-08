"""Unit tests for the ``memrelay observe`` CLI command (session B spool mocked out).

The command wiring is exercised in isolation: session selection, spool opening, and
``run_observe`` are all patched so the test asserts the CLI plumbing (argument routing,
error handling, output) without touching a real Copilot home or session B's spool.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from memrelay import cli
from memrelay.ingest.graphiti_sink import ObserveResult


class _FakeSpool:
    def append(self, record: dict) -> None:  # pragma: no cover - not exercised here
        ...


def test_observe_invokes_run_observe(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}
    ref = SimpleNamespace(session_id="sess-9", path=str(tmp_path / "events.jsonl"))
    fake_spool = _FakeSpool()

    def fake_select(provider, session_id):
        captured["select_arg"] = session_id
        return ref

    async def fake_run_observe(events_path, session_id, *, spool, provider, config):
        captured["events_path"] = events_path
        captured["session_id"] = session_id
        captured["spool"] = spool
        return ObserveResult(
            session_id=session_id,
            namespace="acme",
            repo="acme/widgets",
            parsed=5,
            appended=2,
            skipped=3,
        )

    monkeypatch.setattr(cli, "_select_session", fake_select)
    monkeypatch.setattr(cli, "_open_spool", lambda db_path: fake_spool)
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr("memrelay.ingest.graphiti_sink.run_observe", fake_run_observe)

    result = CliRunner().invoke(cli.main, ["observe", "--session", "sess-9"])

    assert result.exit_code == 0, result.output
    assert captured["select_arg"] == "sess-9"
    assert captured["events_path"] == ref.path
    assert captured["session_id"] == "sess-9"
    assert captured["spool"] is fake_spool
    assert "observed session sess-9" in result.output
    assert "namespace: acme" in result.output
    assert "repo:      acme/widgets" in result.output
    assert "episodes:  2" in result.output


def test_observe_uses_explicit_spool_path(monkeypatch, tmp_path: Path) -> None:
    ref = SimpleNamespace(session_id="s", path=str(tmp_path / "events.jsonl"))
    opened: dict = {}

    async def fake_run_observe(events_path, session_id, *, spool, provider, config):
        return ObserveResult(session_id=session_id, namespace="ns", repo=None)

    monkeypatch.setattr(cli, "_select_session", lambda provider, session_id: ref)
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr("memrelay.ingest.graphiti_sink.run_observe", fake_run_observe)

    def fake_open_spool(db_path):
        opened["db_path"] = Path(db_path)
        return _FakeSpool()

    monkeypatch.setattr(cli, "_open_spool", fake_open_spool)

    spool_arg = tmp_path / "custom" / "spool.db"
    result = CliRunner().invoke(cli.main, ["observe", "--spool", str(spool_arg)])

    assert result.exit_code == 0, result.output
    assert opened["db_path"] == spool_arg


def test_observe_errors_when_no_session(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "_select_session", lambda provider, session_id: None)
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)

    result = CliRunner().invoke(cli.main, ["observe"])

    assert result.exit_code != 0
    assert "no Copilot sessions found" in result.output


def test_observe_errors_when_named_session_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "_select_session", lambda provider, session_id: None)
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)

    result = CliRunner().invoke(cli.main, ["observe", "--session", "ghost"])

    assert result.exit_code != 0
    assert "ghost" in result.output


def test_observe_resolves_provider_via_registry(monkeypatch, tmp_path: Path) -> None:
    """The provider is resolved by ``_resolve_provider`` and threaded to select + run."""
    sentinel = object()
    captured: dict = {}

    def fake_resolve(copilot_home):
        captured["home_arg"] = copilot_home
        return sentinel

    def fake_select(provider, session_id):
        captured["select_provider"] = provider
        return SimpleNamespace(session_id="s", path=str(tmp_path / "events.jsonl"))

    async def fake_run_observe(events_path, session_id, *, spool, provider, config):
        captured["run_provider"] = provider
        return ObserveResult(session_id=session_id, namespace="ns", repo=None)

    monkeypatch.setattr(cli, "_resolve_provider", fake_resolve)
    monkeypatch.setattr(cli, "_select_session", fake_select)
    monkeypatch.setattr(cli, "_open_spool", lambda db_path: _FakeSpool())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr("memrelay.ingest.graphiti_sink.run_observe", fake_run_observe)

    result = CliRunner().invoke(cli.main, ["observe"])

    assert result.exit_code == 0, result.output
    assert captured["select_provider"] is sentinel
    assert captured["run_provider"] is sentinel
