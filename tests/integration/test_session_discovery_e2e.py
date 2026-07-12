"""End-to-end: the daemon's session poller captures a real session into the real spool.

Composes the whole E1-S4 path with **real** pieces — ``active_sessions`` discovery over a
real ``CopilotProvider``, a :class:`~memrelay.daemon.session_discovery.RunObserveCapture`
driving the durable :class:`~memrelay.ingest.spool.Spool` via the idempotent ``run_observe``
— yet stays deterministic: the capture's cadence ``wait`` is injected to fire an event right
after its first observe (no wall-clock sleep), and the poller is driven one ``poll_once``
tick at a time. It proves a newly-active session starts ingestion (three composed episodes
land in the spool), ending the session stops the capture cleanly, and the stop-time drain is
idempotent (no duplicate episodes). Engine-free: the spool is the whole downstream.

Local git/fixture helpers are duplicated here on purpose (the shared conftests are off-limits
to this lane); they mirror ``tests/integration/test_registry_observe.py``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest

from memrelay.config import load_config
from memrelay.daemon.session_discovery import (
    RunObserveCapture,
    SessionDiscoveryPoller,
    active_sessions,
)
from memrelay.providers.registry import get_registry

REMOTE_URL = "https://github.com/acme/widgets.git"


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
    session_id = "disco-session"
    events = home / "session-state" / session_id / "events.jsonl"
    _rewrite_cwd(copilot_fixture, events, str(repo))
    monkeypatch.setenv("MEMRELAY_COPILOT_HOME", str(home))
    # Pin the Claude home away so auto-detect stays copilot-only regardless of this box.
    monkeypatch.setenv("MEMRELAY_CLAUDE_HOME", str(tmp_path / "_no_claude_home"))
    # E12-S5: pin the ten new agent homes away too so ``resolve()`` stays copilot-only here.
    for env_var in (
        "MEMRELAY_CODEX_HOME",
        "MEMRELAY_CONTINUE_HOME",
        "MEMRELAY_CLINE_HOME",
        "MEMRELAY_AIDER_HOME",
        "MEMRELAY_AMAZONQ_HOME",
        "MEMRELAY_GOOSE_HOME",
        "MEMRELAY_OPENCODE_HOME",
        "MEMRELAY_OPENHANDS_HOME",
        "MEMRELAY_SWEAGENT_HOME",
        "MEMRELAY_ANTIGRAVITY_HOME",
    ):
        monkeypatch.setenv(env_var, str(tmp_path / f"_no_home_{env_var.lower()}"))
    return home, session_id


def test_poller_captures_active_session_then_stops_cleanly(
    copilot_home_with_session: tuple[Path, str], tmp_path: Path
) -> None:
    """poll → discover → RunObserveCapture → real Spool; then end → clean, idempotent stop."""
    from memrelay.ingest.spool import Spool

    _, session_id = copilot_home_with_session
    provider = get_registry().resolve()
    cfg = load_config(environ={}, home=str(tmp_path / "home"))
    spool_db = tmp_path / "spool" / "spool.db"

    async def scenario() -> tuple[int, int, list[str], dict]:
        observed = asyncio.Event()

        async def signal_then_park(interval: float, stop: asyncio.Event) -> None:
            # Entered right after each observe pass: fire once (deterministic readiness,
            # no sleep) then park the capture loop until it is stopped.
            observed.set()
            await stop.wait()

        # A live, generous freshness window: the fixture was just written, so it is active.
        active = list(active_sessions(provider, now=time.time(), freshness_s=3600.0))

        with Spool(spool_db) as spool:

            def capture_factory(ref):
                return RunObserveCapture(
                    ref,
                    spool=spool,
                    provider=provider,
                    config=cfg,
                    namespace_map=cfg.namespaces.repo_map,
                    interval=2.0,
                    wait=signal_then_park,
                )

            poller = SessionDiscoveryPoller(
                discover=lambda: list(active),
                capture_factory=capture_factory,
            )

            # Tick 1: the active session is discovered and its capture starts observing.
            await poller.poll_once()
            await asyncio.wait_for(observed.wait(), timeout=10.0)  # one real observe done
            after_start = spool.pending()
            started_stats = poller.stats()
            batch_keys = [record["idempotency_key"] for _, record in spool.read_batch()]

            # Tick 2: the session has ended — its capture is stopped (final drain included).
            active.clear()
            await poller.poll_once()
            after_stop = spool.pending()
            stopped_stats = poller.stats()

        return after_start, after_stop, batch_keys, stopped_stats | started_stats

    after_start, after_stop, batch_keys, _ = asyncio.run(scenario())

    # A newly-active session was captured: three composed episodes (two work-units + a
    # summary), exactly as the shared observe path produces for this fixture.
    assert after_start == 3
    assert len(batch_keys) == 3
    # Ending the session drains once more and stops cleanly — idempotent, no duplicates.
    assert after_stop == 3


def test_poller_stats_track_start_and_clean_stop(
    copilot_home_with_session: tuple[Path, str], tmp_path: Path
) -> None:
    """The poller's health counters move with the live capture set across the lifecycle."""
    from memrelay.ingest.spool import Spool

    provider = get_registry().resolve()
    cfg = load_config(environ={}, home=str(tmp_path / "home"))
    spool_db = tmp_path / "spool" / "spool.db"

    async def scenario() -> tuple[dict, dict]:
        observed = asyncio.Event()

        async def signal_then_park(interval: float, stop: asyncio.Event) -> None:
            observed.set()
            await stop.wait()

        active = list(active_sessions(provider, now=time.time(), freshness_s=3600.0))

        with Spool(spool_db) as spool:

            def capture_factory(ref):
                return RunObserveCapture(
                    ref,
                    spool=spool,
                    provider=provider,
                    config=cfg,
                    namespace_map=cfg.namespaces.repo_map,
                    wait=signal_then_park,
                )

            poller = SessionDiscoveryPoller(
                discover=lambda: list(active), capture_factory=capture_factory
            )
            await poller.poll_once()
            await asyncio.wait_for(observed.wait(), timeout=10.0)
            live = poller.stats()

            active.clear()
            await poller.poll_once()
            ended = poller.stats()

        return live, ended

    live, ended = asyncio.run(scenario())
    assert live == {"sessions_observed": 1, "active_sessions": 1}
    # Cumulative start counter stays; the live set drains to zero on clean stop.
    assert ended == {"sessions_observed": 1, "active_sessions": 0}
