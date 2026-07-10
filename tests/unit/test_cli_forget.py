"""CLI behavior tests for ``memrelay forget`` (E9-S1, issue #58).

Focus on the command contract, not the graph: usage guards, the irreversible
confirmation prompt (declined vs. ``--yes`` vs. answered ``y``), ``--dry-run`` as a
no-op, and the "nothing matched" path. The real engine is exercised by
``tests/integration/test_forget.py``; here the engine is replaced with a fake that
records how ``forget`` was called so we can assert *whether* a destructive delete
was issued.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

import memrelay.engine.graphiti as engine_mod
from memrelay.cli import main


class _FakeEngine:
    """Stand-in ``MemoryEngine`` that records ``forget`` calls and returns a fixed count."""

    def __init__(self, count: int) -> None:
        self._count = count
        self.forget_calls: list[tuple[str | None, str | None, bool]] = []
        self.closed = False

    async def forget(
        self,
        *,
        repo: str | None = None,
        namespace: str | None = None,
        dry_run: bool = False,
    ) -> int:
        self.forget_calls.append((repo, namespace, dry_run))
        return self._count

    async def close(self) -> None:
        self.closed = True


def _patch_engine(monkeypatch, count: int) -> _FakeEngine:
    """Route ``forget`` at a fake engine and stub config loading; return the fake."""
    fake = _FakeEngine(count)

    async def _fake_from_config(cfg, **kwargs):
        return fake

    monkeypatch.setattr(engine_mod.MemoryEngine, "from_config", staticmethod(_fake_from_config))
    monkeypatch.setattr("memrelay.cli.load_config", lambda: None)
    return fake


def _real_deletes(fake: _FakeEngine) -> list[tuple[str | None, str | None, bool]]:
    """The destructive (non-dry-run) forget calls that actually happened."""
    return [call for call in fake.forget_calls if call[2] is False]


def test_forget_listed_in_help() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "forget" in result.output


def test_forget_requires_a_target() -> None:
    result = CliRunner().invoke(main, ["forget"])
    assert result.exit_code == 2
    assert "--repo" in result.output and "--namespace" in result.output


def test_forget_rejects_both_targets() -> None:
    result = CliRunner().invoke(main, ["forget", "--repo", "owner/name", "--namespace", "team"])
    assert result.exit_code == 2
    assert "only one" in result.output


def test_forget_declined_prompt_deletes_nothing(monkeypatch) -> None:
    fake = _patch_engine(monkeypatch, count=3)
    result = CliRunner().invoke(main, ["forget", "--repo", "owner/name"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "IRREVERSIBLE" in result.output
    assert "aborted: nothing deleted." in result.output
    # The count was probed (dry-run) but no destructive delete was issued.
    assert ("owner/name", None, True) in fake.forget_calls
    assert _real_deletes(fake) == []
    assert fake.closed is True


def test_forget_yes_bypasses_prompt_and_deletes(monkeypatch) -> None:
    fake = _patch_engine(monkeypatch, count=3)
    result = CliRunner().invoke(main, ["forget", "--repo", "owner/name", "--yes"])
    assert result.exit_code == 0, result.output
    assert "deleted 3 episode(s) for repo 'owner/name'." in result.output
    # No prompt was shown, and a destructive delete was issued for the repo.
    assert "Proceed?" not in result.output
    assert _real_deletes(fake) == [("owner/name", None, False)]


def test_forget_confirmed_prompt_deletes_namespace(monkeypatch) -> None:
    fake = _patch_engine(monkeypatch, count=2)
    result = CliRunner().invoke(main, ["forget", "--namespace", "team"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "IRREVERSIBLE" in result.output
    assert "deleted 2 episode(s) for namespace 'team'." in result.output
    assert _real_deletes(fake) == [(None, "team", False)]


def test_forget_dry_run_reports_without_deleting(monkeypatch) -> None:
    fake = _patch_engine(monkeypatch, count=5)
    result = CliRunner().invoke(main, ["forget", "--namespace", "team", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would delete 5 episode(s) for namespace 'team'" in result.output
    assert "nothing deleted" in result.output
    # No prompt, no destructive delete — only the dry-run probe ran.
    assert "IRREVERSIBLE" not in result.output
    assert _real_deletes(fake) == []


def test_forget_reports_when_nothing_matches(monkeypatch) -> None:
    fake = _patch_engine(monkeypatch, count=0)
    result = CliRunner().invoke(main, ["forget", "--repo", "owner/name", "--yes"])
    assert result.exit_code == 0, result.output
    assert "no memories found for repo 'owner/name'; nothing deleted." in result.output
    # A zero blast radius short-circuits before any destructive delete.
    assert _real_deletes(fake) == []


def test_forget_surfaces_engine_open_failure(monkeypatch) -> None:
    async def _boom(cfg, **kwargs):
        raise RuntimeError("graph is locked")

    monkeypatch.setattr(engine_mod.MemoryEngine, "from_config", staticmethod(_boom))
    monkeypatch.setattr("memrelay.cli.load_config", lambda: None)

    result = CliRunner().invoke(main, ["forget", "--repo", "owner/name", "--yes"])
    assert result.exit_code != 0
    assert "could not open the memory graph" in result.output
    assert "graph is locked" in result.output


if __name__ == "__main__":  # pragma: no cover - convenience only
    raise SystemExit(pytest.main([__file__, "-q"]))
