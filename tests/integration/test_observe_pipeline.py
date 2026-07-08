"""End-to-end observe pipeline: real fixture + real git remote -> episode records.

Unlike the unit tests (which construct ``SessionEvent``s directly), this drives the
*whole* observe path — ``CopilotProvider`` adapter/source, the traceforge
``EventPipeline`` + ``Enricher``, and :class:`GraphitiSink` — over the committed
Copilot fixture, and proves the namespace/repo are resolved from a **real git remote**
via the exact ``resolve_context`` that ``memory_recall`` uses (SPEC §5.2). The first
three tests inject a fake spool + fake ``idempotency_fn`` / ``record_factory`` to stay
independent of session B (fast, B-agnostic). The final test
(:func:`test_observe_writes_to_real_spool`) drives the **real** session-B ``Spool`` +
``EpisodeRecord.new`` + ``make_idempotency_key`` end-to-end now that B is merged to
main, proving the observe→durable-spool path and its idempotency against real SQLite.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from memrelay.ingest.graphiti_sink import run_observe
from memrelay.mcp.namespace import resolve_context

REMOTE_URL = "https://github.com/acme/widgets.git"


class FakeSpool:
    """Duck-typed stand-in for session B's durable ``Spool``."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def append(self, record: dict) -> None:
        self.records.append(record)


def _fake_idem(session_id: str | None, event_id: str | None, content: str) -> str:
    return f"K|{session_id}|{event_id}|{content}"


def _fake_factory(**fields: object) -> dict:
    return dict(fields)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _make_repo(path: Path) -> Path:
    """A minimal git repo whose ``origin`` remote drives namespace resolution."""
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
    dest.write_text("\n".join(lines_out) + "\n", encoding="utf-8")


@pytest.fixture
def observed_session(tmp_path: Path, copilot_fixture: Path) -> tuple[Path, str]:
    """A fixture events.jsonl whose session ran inside a real ``acme/widgets`` repo."""
    repo = _make_repo(tmp_path / "widgets")
    events = tmp_path / "events.jsonl"
    _rewrite_cwd(copilot_fixture, events, str(repo))
    return events, str(repo)


def test_observe_resolves_namespace_from_git_remote(observed_session) -> None:
    events, repo_cwd = observed_session

    # The namespace/repo memory is stored under MUST equal what recall recomputes.
    assert resolve_context(repo_cwd) == ("acme", "acme/widgets")

    spool = FakeSpool()
    result = run_observe_sync(events, "obs-session", spool, cwd=None)

    assert result.namespace == "acme"
    assert result.repo == "acme/widgets"
    assert result.appended == 1

    (record,) = spool.records
    assert record["namespace"] == "acme"
    assert record["repo"] == "acme/widgets"
    assert record["source"] == "copilot"
    assert record["session_id"] == "obs-session"
    assert record["content"]  # non-empty conversational text
    assert record["event_id"]  # stable wire id present
    assert record["ts"]  # ISO-8601 timestamp


def test_observe_is_idempotent_across_two_runs(observed_session) -> None:
    """Re-observing the same session produces byte-identical records (stable keys)."""
    events, _ = observed_session

    first = FakeSpool()
    second = FakeSpool()
    run_observe_sync(events, "obs-session", first, cwd=None)
    run_observe_sync(events, "obs-session", second, cwd=None)

    assert first.records == second.records
    assert len(first.records) == 1
    assert first.records[0]["idempotency_key"] == second.records[0]["idempotency_key"]


def test_observe_can_override_cwd(observed_session, tmp_path: Path) -> None:
    """An explicit ``cwd`` overrides the trace's own — used by the daemon per session."""
    events, _ = observed_session
    other = _make_repo(tmp_path / "other")
    _git("remote", "set-url", "origin", "git@github.com:globex/gadgets.git", cwd=other)

    spool = FakeSpool()
    result = run_observe_sync(events, "obs-session", spool, cwd=str(other))

    assert result.namespace == "globex"
    assert result.repo == "globex/gadgets"
    assert spool.records[0]["namespace"] == "globex"


def test_observe_writes_to_real_spool(observed_session, tmp_path: Path) -> None:
    """End-to-end against session B's REAL durable ``Spool`` (post-merge, no fakes).

    ``run_observe`` builds records with B's ``EpisodeRecord.new`` +
    ``make_idempotency_key`` and appends them to a real SQLite spool laid out at the
    frozen ``<home>/spool/spool.db`` path. Proves the whole observe→spool path and that
    re-observing the same session is idempotent (B's ``INSERT OR IGNORE`` on the stable
    ``idempotency_key``), so the daemon ingester never double-ingests.
    """
    from memrelay.ingest.episode import make_idempotency_key
    from memrelay.ingest.spool import Spool

    events, _ = observed_session
    # Mirror the frozen daemon-ingester layout: <home>/spool/spool.db.
    spool_db = tmp_path / "spool" / "spool.db"

    with Spool(spool_db) as spool:
        result = real_observe_sync(events, "obs-session", spool, cwd=None)

        assert result.namespace == "acme"
        assert result.repo == "acme/widgets"
        assert result.appended == 1
        assert spool.pending() == 1

        batch = spool.read_batch()
        assert len(batch) == 1
        _, record = batch[0]
        assert record["namespace"] == "acme"
        assert record["repo"] == "acme/widgets"
        assert record["source"] == "copilot"
        assert record["session_id"] == "obs-session"
        assert record["content"]  # non-empty conversational text
        assert record["event_id"]  # stable wire id
        assert record["ts"]  # ISO-8601 timestamp
        # The key is B's real sha256 over the SAME (session_id, stable event_id, content).
        assert record["idempotency_key"] == make_idempotency_key(
            "obs-session", record["event_id"], record["content"]
        )
        first_key = record["idempotency_key"]

    # Re-observe from a fresh connection (simulating a second run/process): the durable
    # unique-key guard means the episode is NOT re-appended.
    with Spool(spool_db) as spool2:
        assert spool2.pending() == 1
        real_observe_sync(events, "obs-session", spool2, cwd=None)
        assert spool2.pending() == 1
        batch2 = spool2.read_batch()
        assert len(batch2) == 1
        assert batch2[0][1]["idempotency_key"] == first_key


def run_observe_sync(events: Path, session_id: str, spool: FakeSpool, *, cwd: str | None):
    """Drive the async ``run_observe`` synchronously with the fake idempotency fn."""
    import asyncio

    return asyncio.run(
        run_observe(
            events,
            session_id,
            spool=spool,
            cwd=cwd,
            idempotency_fn=_fake_idem,
            record_factory=_fake_factory,
        )
    )


def real_observe_sync(events: Path, session_id: str, spool, *, cwd: str | None):
    """Drive ``run_observe`` with session B's REAL episode/idempotency helpers (no fakes)."""
    import asyncio

    return asyncio.run(run_observe(events, session_id, spool=spool, cwd=cwd))
