#!/usr/bin/env python3
r"""Bounded, isolated live e2e for the borrow-host self-observation loop fix.

This proves — with the **real** ``copilot`` CLI, no mocks — that memrelay's zero-key
borrow-host Copilot extraction path (a) still works end-to-end (a synthetic Copilot
session is observed, extracted, and recalled from the graph) and (b) no longer feeds
back on itself, because memrelay now controls each extraction session's id
(``copilot --session-id <uuid> -p ...``), registers it in
:mod:`memrelay.internal_sessions`, and excludes it from
:func:`memrelay.daemon.session_discovery.active_sessions`.

HARD SAFETY RULE (why this is safe to run on a real Copilot box)
---------------------------------------------------------------
The real ``~/.copilot`` holds tens of thousands of sessions, including live ones.
Observing it with a running daemon would trigger the exact loop this fix closes. So
this driver NEVER starts a live daemon/poller. It:

* points the *observed* Copilot home at a throwaway scratch dir via
  ``MEMRELAY_COPILOT_HOME`` (hard-asserted != real ``~/.copilot``), containing exactly
  ONE small synthetic fixture session, and
* drives the pipeline **deterministically and once** in-process:
  ``run_observe(fixture)`` -> durable spool -> the daemon's Ingester (drained once,
  which performs the REAL Copilot extraction) -> ``engine.search`` recall.

The real ``copilot -p`` extraction still writes its own session-state under the real
``~/.copilot`` (the CLI ignores ``MEMRELAY_COPILOT_HOME`` — that var only steers what
memrelay *observes*), but nothing observes the real home here, so there is no cadence,
no re-observation, and no loop. Cost is bounded to one fixture session = a handful of
real (premium/Opus-tier) Copilot calls.

Run it (from the repo root) against THIS worktree's source:

    $env:PYTHONPATH=(Resolve-Path .\src).Path; python scripts\smoke_selfobserve_e2e.py

Exit codes: ``0`` success (fixture recalled AND memrelay registered its own extraction
session id); non-zero on any failure or missing prerequisite. There is no forced green.

Additive by design: this file lives under ``scripts/`` (pytest ``testpaths = ["tests"]``),
so it is never collected as a test. It mirrors the ``scripts/smoke_e2e.py`` +
``docs/SMOKE.md`` pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Ensure THIS worktree's ``src`` wins over any editable install that may point at a
# sibling worktree, so the fix under test is the code actually exercised.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from memrelay import internal_sessions  # noqa: E402
from memrelay.config import load_config  # noqa: E402
from memrelay.daemon.runtime import default_ingester_factory  # noqa: E402
from memrelay.daemon.session_discovery import (  # noqa: E402
    active_sessions,
    warn_on_self_observation,
)
from memrelay.engine.graphiti import MemoryEngine  # noqa: E402
from memrelay.ingest.graphiti_sink import run_observe  # noqa: E402
from memrelay.ingest.spool import Spool  # noqa: E402
from memrelay.providers.copilot import (  # noqa: E402
    DEFAULT_COPILOT_HOME,
    EVENTS_FILENAME,
    SESSION_STATE_DIR,
    CopilotProvider,
)

log = logging.getLogger("smoke_selfobserve")

# ─── A distinctive, recallable fact (rare tokens make the round-trip unambiguous) ──
REMOTE_URL = "https://github.com/acme/widgets.git"
FACT = (
    "Project note for memrelay: the Zephyrine ingestion service is owned by engineer "
    "Dana Okonkwo, and it persists every widget telemetry record into the Ladybug graph."
)
RECALL_QUERY = "Who owns the Zephyrine ingestion service and where does it store telemetry?"
RECALL_TOKENS = ("zephyrine", "dana", "okonkwo", "ladybug")

DRAIN_TIMEOUT = 900.0  # a real Copilot extraction (premium/Opus tier) makes several calls/episode


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _make_repo(path: Path) -> Path:
    """A minimal git repo whose ``origin`` remote drives deterministic namespace resolution."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    _git("remote", "add", "origin", REMOTE_URL, cwd=path)
    return path


def _write_fixture_session(events: Path, *, cwd: str) -> None:
    """Author ONE realistic Copilot ``events.jsonl`` carrying a real, recallable fact.

    Shapes mirror ``tests/fixtures/copilot_session.jsonl`` (session.start, a user turn, an
    assistant turn with a tool request, a tool execution, turn end, session shutdown), but
    with real content instead of ``[redacted]`` so extraction produces graph nodes/edges.
    """
    sid = "fixture-0000-4000-8000-000000000000"
    turn = "fixture-turn-0000-0000-000000000001"
    now = "2026-06-29T17:47:20.282Z"
    events_list = [
        {
            "type": "session.start",
            "data": {"sessionId": sid, "version": 1, "context": {"cwd": cwd}},
            "id": f"{sid}-start",
            "timestamp": now,
            "parentId": None,
        },
        {
            "type": "user.message",
            "data": {"content": FACT, "attachments": []},
            "id": f"{sid}-user",
            "timestamp": "2026-06-29T17:47:21.000Z",
            "parentId": f"{sid}-start",
        },
        {
            "type": "assistant.turn_start",
            "data": {"turnId": turn},
            "id": f"{sid}-turnstart",
            "timestamp": "2026-06-29T17:47:21.100Z",
            "parentId": f"{sid}-user",
        },
        {
            "type": "assistant.message",
            "data": {
                "messageId": f"{sid}-assistant",
                "model": "claude-sonnet-4.6",
                "content": (
                    "Recorded: Dana Okonkwo owns the Zephyrine ingestion service, which writes "
                    "widget telemetry into the Ladybug graph."
                ),
                "toolRequests": [
                    {
                        "toolCallId": f"{sid}-tool",
                        "name": "powershell",
                        "arguments": {"command": "Get-Service Zephyrine"},
                        "type": "function",
                    }
                ],
                "turnId": turn,
            },
            "id": f"{sid}-assistant-evt",
            "timestamp": "2026-06-29T17:47:22.000Z",
            "parentId": f"{sid}-turnstart",
        },
        {
            "type": "tool.execution_start",
            "data": {
                "toolCallId": f"{sid}-tool",
                "toolName": "powershell",
                "arguments": {"command": "Get-Service Zephyrine"},
                "turnId": turn,
            },
            "id": f"{sid}-toolstart",
            "timestamp": "2026-06-29T17:47:22.100Z",
            "parentId": f"{sid}-assistant-evt",
        },
        {
            "type": "tool.execution_complete",
            "data": {
                "toolCallId": f"{sid}-tool",
                "turnId": turn,
                "success": True,
                "result": {"content": "Status: Running  Name: Zephyrine"},
            },
            "id": f"{sid}-toolcomplete",
            "timestamp": "2026-06-29T17:47:22.900Z",
            "parentId": f"{sid}-toolstart",
        },
        {
            "type": "assistant.turn_end",
            "data": {"turnId": turn},
            "id": f"{sid}-turnend",
            "timestamp": "2026-06-29T17:47:23.000Z",
            "parentId": f"{sid}-toolcomplete",
        },
        {
            "type": "session.shutdown",
            "data": {"shutdownType": "routine"},
            "id": f"{sid}-shutdown",
            "timestamp": "2026-06-29T17:49:56.805Z",
            "parentId": f"{sid}-turnend",
        },
    ]
    events.write_text("".join(json.dumps(e) + "\n" for e in events_list), encoding="utf-8")


def _fail(msg: str) -> None:
    log.error("FAIL: %s", msg)
    raise SystemExit(1)


def _preflight(scratch: Path) -> tuple[Path, Path, Path]:
    """Scrub API keys, require the host CLI, and set up isolated scratch homes (SAFE)."""
    # Strip every ``*_API_KEY`` from the process env so a green can NEVER be silently
    # attributed to an inherited key (byo-key/OpenAI could otherwise mask the borrow-host
    # path). We scrub rather than refuse: unrelated keys (ElevenLabs, Gemini, …) are common on
    # a dev box and must not block a borrow-host proof. The real ``copilot`` CLI authenticates
    # on its own and does not use these.
    scrubbed = sorted(k for k in list(os.environ) if k.endswith("_API_KEY"))
    for key in scrubbed:
        os.environ.pop(key, None)
    if scrubbed:
        log.info("scrubbed %d *_API_KEY var(s) from env: %s", len(scrubbed), scrubbed)

    if shutil.which("copilot") is None:
        _fail("the real 'copilot' CLI is required on PATH for this live borrow-host e2e")

    memrelay_home = scratch / "memrelay-home"
    copilot_home = scratch / "copilot-home"
    (copilot_home / SESSION_STATE_DIR).mkdir(parents=True, exist_ok=True)
    memrelay_home.mkdir(parents=True, exist_ok=True)

    # HARD SAFETY: the observed Copilot home must NOT be the real one.
    real = Path(DEFAULT_COPILOT_HOME).expanduser().resolve()
    if copilot_home.resolve() == real:
        _fail("scratch copilot home resolved to the REAL ~/.copilot — aborting to avoid the loop")

    os.environ["MEMRELAY_HOME"] = str(memrelay_home)
    os.environ["MEMRELAY_COPILOT_HOME"] = str(copilot_home)
    log.info("isolated MEMRELAY_HOME       = %s", memrelay_home)
    log.info("isolated MEMRELAY_COPILOT_HOME = %s (real ~/.copilot = %s)", copilot_home, real)
    return memrelay_home, copilot_home, real


def _prove_guard(cfg) -> None:
    """Part B: the circular-config guard fires for the zero-key Copilot default."""
    warned = warn_on_self_observation("copilot", cfg)
    if not warned:
        _fail("circular-config guard did NOT fire for borrow-host + host=copilot")
    log.info("guard: warn_on_self_observation('copilot', cfg) -> True (warned, as expected)")


def _prove_exclusion(copilot_home: Path, fixture_id: str, fixture_events: Path) -> None:
    """Part A: the REAL provider's active_sessions excludes a registered extraction id.

    No daemon runs. We drive the production ``active_sessions`` predicate directly against a
    real :class:`CopilotProvider` rooted at the scratch home, and show that registering an id
    (exactly what the borrow-host path does before spawning ``copilot``) drops it even though
    its trace is brand-new.
    """
    provider = CopilotProvider.from_home(None)  # honors MEMRELAY_COPILOT_HOME
    if provider.session_state_root.resolve() != (copilot_home / SESSION_STATE_DIR).resolve():
        _fail("provider did not resolve to the scratch home — isolation broken")

    before = {r.session_id for r in active_sessions(provider, now=time.time(), freshness_s=30.0)}
    if fixture_id not in before:
        _fail(f"fixture session {fixture_id!r} was not discovered as active: {sorted(before)}")

    # Simulate an extraction session landing in the OBSERVED tree with a fresh trace, then
    # register it exactly as the borrow-host path does. (Real copilot writes to the real home;
    # we materialize one here only to prove the exclusion on the real provider + filesystem.)
    extraction_id = str(uuid.uuid4())
    extra_dir = copilot_home / SESSION_STATE_DIR / extraction_id
    extra_dir.mkdir(parents=True, exist_ok=True)
    (extra_dir / EVENTS_FILENAME).write_text("{}\n", encoding="utf-8")

    fresh = {r.session_id for r in active_sessions(provider, now=time.time(), freshness_s=30.0)}
    if extraction_id not in fresh:
        _fail("control failed: a fresh unregistered session should be active before registration")

    internal_sessions.register(extraction_id)
    after = {r.session_id for r in active_sessions(provider, now=time.time(), freshness_s=30.0)}
    if extraction_id in after:
        _fail(f"registered extraction id {extraction_id!r} was NOT excluded by active_sessions")
    if fixture_id not in after:
        _fail("exclusion wrongly dropped the genuine fixture session")
    log.info(
        "exclusion: fixture %s stays active; registered extraction id %s is excluded (loop closed)",
        fixture_id,
        extraction_id,
    )


async def _drain_once(ingester, *, timeout: float) -> None:
    """Run the daemon's ingester until the spool is fully consumed, then stop it cleanly."""
    stop = asyncio.Event()
    task = asyncio.create_task(ingester.run(stop))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while ingester.stats()["spool_pending"] > 0 and loop.time() < deadline:
        await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=timeout)


async def _run(scratch: Path) -> int:
    memrelay_home, copilot_home, _real = _preflight(scratch)

    # A temp git repo so run_observe derives a deterministic namespace from origin.
    repo = _make_repo(scratch / "widgets")

    fixture_id = str(uuid.uuid4())
    fixture_dir = copilot_home / SESSION_STATE_DIR / fixture_id
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_events = fixture_dir / EVENTS_FILENAME
    _write_fixture_session(fixture_events, cwd=str(repo))

    graph_path = memrelay_home / "graph.db"
    cfg = load_config(
        environ={},
        home=str(memrelay_home),
        graph={"path": str(graph_path), "backend": "ladybug"},
    )
    if cfg.llm.strategy != "borrow-host" or cfg.llm.host != "copilot":
        _fail(
            f"expected zero-key borrow-host+copilot default, got {cfg.llm.strategy}/{cfg.llm.host}"
        )

    _prove_guard(cfg)
    _prove_exclusion(copilot_home, fixture_id, fixture_events)

    # ── happy path: observe -> spool -> ingester (REAL copilot extraction) -> recall ──
    spool_path = cfg.home_path / "spool" / "spool.db"
    observe_spool = Spool(spool_path)
    try:
        result = await run_observe(fixture_events, fixture_id, spool=observe_spool, config=cfg)
        namespace = result.namespace
        log.info(
            "observe: appended=%s namespace=%r repo=%r pending=%s",
            result.appended,
            namespace,
            result.repo,
            observe_spool.pending(),
        )
        if observe_spool.pending() < 1:
            _fail("observe produced no spooled episodes")
    finally:
        observe_spool.close()

    engine = await MemoryEngine.from_config(cfg)  # REAL borrow-host copilot + real embedder
    try:
        ingester = default_ingester_factory(engine, cfg)
        if ingester is None:
            _fail("default_ingester_factory returned None (ingest seams missing)")

        internal_before = internal_sessions.snapshot()
        log.info("registered internal ids BEFORE real extraction: %d", len(internal_before))

        log.info("draining the spool once — this performs the REAL Copilot extraction …")
        t0 = time.time()
        await _drain_once(ingester, timeout=DRAIN_TIMEOUT)
        log.info(
            "drain complete in %.1fs: episodes_ingested=%s spool_pending=%s",
            time.time() - t0,
            ingester.stats()["episodes_ingested"],
            ingester.stats()["spool_pending"],
        )

        # Part A, LIVE: the real extraction registered its own copilot session id(s).
        internal_after = internal_sessions.snapshot()
        new_ids = internal_after - internal_before
        log.info(
            "registered internal ids AFTER real extraction: %d (new: %d)",
            len(internal_after),
            len(new_ids),
        )
        if not new_ids:
            _fail("real borrow-host extraction registered NO internal session id — Part A not live")

        # ── recall ──
        results = await engine.search(RECALL_QUERY, namespace=namespace)
        nodes = results.get("nodes", [])
        edges = results.get("edges", [])
        blob = " ".join(f"{n.get('name') or ''} {n.get('summary') or ''}" for n in nodes)
        blob += " " + " ".join(f"{e.get('name') or ''} {e.get('fact') or ''}" for e in edges)

        print("\n================= SELF-OBSERVE E2E EVIDENCE =================")
        print(f"namespace                : {namespace!r}")
        print(f"episodes_ingested        : {ingester.stats()['episodes_ingested']}")
        print(f"graph nodes recalled     : {len(nodes)}")
        print(f"graph edges recalled     : {len(edges)}")
        print(f"new internal ids (Part A): {len(new_ids)}  (e.g. {sorted(new_ids)[:3]})")
        print(f"recall query             : {RECALL_QUERY!r}")
        print("recalled node names      :")
        for n in nodes:
            print(f"   - {n.get('name')!r}: {(n.get('summary') or '').strip()[:160]}")
        print("recalled edge facts      :")
        for e in edges:
            print(f"   - {(e.get('fact') or '').strip()[:160]}")
        print("============================================================\n")

        if not nodes and not edges:
            _fail("recall returned no nodes AND no edges after real extraction")
        hit = any(tok in blob.lower() for tok in RECALL_TOKENS)
        if not hit:
            _fail(f"recalled graph did not mention any of {RECALL_TOKENS}: {blob[:300]!r}")
        log.info("recall matched expected fact tokens — happy path proven")
    finally:
        await engine.close()

    log.info("SUCCESS: fixture observed+extracted+recalled, and memrelay excluded its own session")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    scratch = Path(tempfile.mkdtemp(prefix="memrelay-selfobserve-"))
    log.info("scratch root: %s", scratch)
    try:
        return asyncio.run(_run(scratch))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        internal_sessions.reset()


if __name__ == "__main__":
    raise SystemExit(main())
