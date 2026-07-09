"""End-to-end: resolve a provider through the registry, then observe → real spool.

This is the integration proof the E12 story demands — that the existing observe path
still works **through the registry seam**, not just a fake. It builds a real
``~/.copilot/session-state/<id>/events.jsonl`` layout inside a real git repo, points
``MEMRELAY_COPILOT_HOME`` at it, and drives:

    get_registry().resolve() → provider.discover_sessions() → run_observe(provider=…)
    → session B's real durable ``Spool``

asserting the namespace/repo resolve from the real git remote and that a re-run through
the registry is idempotent. A companion test proves ``run_observe``'s **no-provider
fallback** now constructs the default provider via the registry too.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from memrelay.ingest.graphiti_sink import run_observe
from memrelay.providers import CopilotProvider
from memrelay.providers.registry import get_registry

REMOTE_URL = "https://github.com/acme/widgets.git"


class FakeSpool:
    """Duck-typed stand-in for session B's durable ``Spool`` (fallback test only)."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def append(self, record: dict) -> None:
        self.records.append(record)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    _git("remote", "add", "origin", REMOTE_URL, cwd=path)
    return path


def _rewrite_cwd(fixture: Path, dest: Path, new_cwd: str) -> None:
    """Copy ``fixture`` to ``dest``, pointing its ``session.start`` cwd at ``new_cwd``."""
    lines_out: list[str] = []
    with open(fixture, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            record = json.loads(stripped)
            if record.get("type") == "session.start":
                record.setdefault("data", {}).setdefault("context", {})["cwd"] = new_cwd
            lines_out.append(json.dumps(record))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines_out) + "\n", encoding="utf-8")


@pytest.fixture
def copilot_home_with_session(
    tmp_path: Path, copilot_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """A real ``~/.copilot`` home whose one session ran inside an ``acme/widgets`` repo."""
    repo = _make_repo(tmp_path / "widgets")
    home = tmp_path / "copilot"
    session_id = "obs-session"
    events = home / "session-state" / session_id / "events.jsonl"
    _rewrite_cwd(copilot_fixture, events, str(repo))
    monkeypatch.setenv("MEMRELAY_COPILOT_HOME", str(home))
    # Pin the Claude home to a non-existent path so ``resolve()`` auto-detect stays
    # copilot-only regardless of whether Claude Code is installed on this machine (the
    # ClaudeCodeProvider from #70 now also self-registers and detects via ~/.claude).
    monkeypatch.setenv("MEMRELAY_CLAUDE_HOME", str(tmp_path / "_no_claude_home"))
    return home, session_id


def test_registry_resolve_discovers_the_real_session(copilot_home_with_session) -> None:
    """``resolve()`` auto-detects the real Copilot home and enumerates its session."""
    home, session_id = copilot_home_with_session

    provider = get_registry().resolve()

    assert isinstance(provider, CopilotProvider)
    assert provider.is_present()
    refs = list(provider.discover_sessions())
    assert [r.session_id for r in refs] == [session_id]
    assert refs[0].agent_id == "copilot"
    assert Path(refs[0].path).is_file()


def test_registry_observe_writes_to_real_spool(copilot_home_with_session, tmp_path: Path) -> None:
    """resolve → discover → run_observe(provider=…) → real ``Spool``, idempotent re-run."""
    from memrelay.ingest.episode import make_idempotency_key
    from memrelay.ingest.spool import Spool

    _, session_id = copilot_home_with_session
    provider = get_registry().resolve()
    (ref,) = provider.discover_sessions()

    spool_db = tmp_path / "spool" / "spool.db"
    with Spool(spool_db) as spool:
        result = asyncio.run(run_observe(ref.path, ref.session_id, spool=spool, provider=provider))

        assert result.namespace == "acme"
        assert result.repo == "acme/widgets"
        # Composed episodes (two work-units + a summary), not one-per-event.
        assert result.appended == 3
        assert spool.pending() == 3

        batch = spool.read_batch()
        assert len(batch) == 3
        records = [record for _, record in batch]
        for record in records:
            assert record["namespace"] == "acme"
            assert record["repo"] == "acme/widgets"
            assert record["source"] == "copilot"
            assert record["session_id"] == session_id
            assert record["content"]
            assert record["event_id"]
            assert record["ts"]
            assert record["idempotency_key"] == make_idempotency_key(
                session_id, record["event_id"], record["content"]
            )
        first_keys = [record["idempotency_key"] for record in records]

    # Re-resolve + re-observe from a fresh connection: the durable unique-key guard
    # means the composed episodes are NOT re-appended (proves the seam preserves
    # idempotency across re-observation of the same session).
    with Spool(spool_db) as spool2:
        assert spool2.pending() == 3
        provider2 = get_registry().resolve()
        asyncio.run(run_observe(ref.path, ref.session_id, spool=spool2, provider=provider2))
        assert spool2.pending() == 3
        assert [record["idempotency_key"] for _, record in spool2.read_batch()] == first_keys


def test_run_observe_fallback_constructs_provider_via_registry(
    tmp_path: Path, copilot_fixture: Path
) -> None:
    """``run_observe`` with no ``provider`` builds the default provider through the registry.

    Proves the ``graphiti_sink`` fallback now routes through ``get_registry().create``
    (not a hardcoded ``CopilotProvider()``) yet yields the identical mapped episode.
    """
    repo = _make_repo(tmp_path / "widgets")
    events = tmp_path / "events.jsonl"
    _rewrite_cwd(copilot_fixture, events, str(repo))

    spool = FakeSpool()
    result = asyncio.run(
        run_observe(
            events,
            "obs-session",
            spool=spool,
            cwd=None,
            idempotency_fn=lambda s, e, c: f"K|{s}|{e}|{c}",
            record_factory=lambda **f: dict(f),
        )
    )

    assert result.namespace == "acme"
    assert result.repo == "acme/widgets"
    assert result.appended == 3
    assert all(record["source"] == "copilot" for record in spool.records)
