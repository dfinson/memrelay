"""Headline cross-agent recall: Copilot + Claude Code unify in ONE namespace graph (#70).

This is the whole claim of the second-provider story — that memrelay's agent abstraction is
real, not Copilot-shaped. Two *different* agents (a synthetic Copilot ``events.jsonl`` and a
synthetic Claude Code ``*.jsonl`` session log) are each observed through their **own
provider** — ``CopilotProvider`` and ``ClaudeCodeProvider`` — while working in the **same git
repo**. The namespace is **derived** from that shared repo's git remote via the real
``resolve_context`` path (the exact function ``memory_recall`` uses), not passed in as a
hard-coded equal string. Both agents' facts land in one embedded-Kuzu graph under that one
derived namespace, and a single ``engine.search`` recalls a fact contributed by *each* agent.

Integrity notes (deliberate, surfaced rather than papered over):

* The Copilot session's namespace is derived from *its own file*: ``run_observe`` reads the
  cwd from the Copilot ``session.start`` record via ``resolve_session_cwd``.
* ``resolve_session_cwd`` is Copilot-shaped (it keys on ``type == "session.start"``); a Claude
  log has no such record (its cwd lives on each turn's top-level ``cwd``). So for the Claude
  observe we pass an explicit ``cwd=<repo>`` — still the **same real repo**, so the namespace
  is genuinely *derived* by ``resolve_context`` from that repo's git remote, and we assert it
  equals the Copilot-derived namespace. Wiring ``memrelay observe`` to read Claude's per-turn
  cwd is a separate observe-seam follow-up, out of scope for #70.

Fully hermetic: deterministic mock LLM + real/offline embedder from ``conftest.py``, a temp
git repo for namespace resolution, embedded Kuzu on ``tmp_path``. No network, no API key,
never a real ``~/.memrelay`` / ``~/.copilot`` / ``~/.claude``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from memrelay.config import load_config
from memrelay.daemon.runtime import default_ingester_factory
from memrelay.engine.graphiti import MemoryEngine
from memrelay.ingest.graphiti_sink import run_observe
from memrelay.ingest.spool import Spool
from memrelay.providers.claude_code import ClaudeCodeProvider
from memrelay.providers.copilot import CopilotProvider

REMOTE_URL = "https://github.com/acme/widgets.git"
EXPECTED_NAMESPACE = "acme"
EXPECTED_REPO = "acme/widgets"

# Each fact carries a unique, unusual term so recall can prove *which* agent contributed it,
# plus the shared "widget" anchor tying them to the same repo/namespace.
COPILOT_FACT = "The widget service authentication is handled by the Zephyr token module."
CLAUDE_FACT = "The widget service health checks are monitored by the Quasar watchdog daemon."
# Each query names the distinctive entity that agent's fact introduced, and hits the SAME
# single namespace graph (group_ids=[namespace]). Naming the entity keeps recall deterministic
# under BOTH the real fastembed embedder and the offline hashing fallback (the invented terms
# have no semantic neighbours, unlike a real word like "Kuzu"). The proof stands: two different
# agents, ingested via two different providers into ONE derived namespace, each contribute a
# fact that the unified graph recalls.
COPILOT_QUERY = "Zephyr token module used for authentication"
CLAUDE_QUERY = "Quasar watchdog daemon that monitors health"
VOCAB = ["Zephyr", "Quasar", "widget"]


def _recall_blob(results: dict) -> str:
    assert set(results) == {"nodes", "edges", "scores"}, f"bad shape: {results!r}"
    blob = " ".join(f"{n.get('name') or ''} {n.get('summary') or ''}" for n in results["nodes"])
    blob += " " + " ".join(f"{e.get('name') or ''} {e.get('fact') or ''}" for e in results["edges"])
    return blob.lower()


def _git(*args: str, cwd: Path) -> None:
    import subprocess

    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    _git("remote", "add", "origin", REMOTE_URL, cwd=path)
    return path


def _write_copilot_session(dest: Path, *, cwd: str, content: str) -> None:
    """A minimal raw Copilot ``events.jsonl``: ``session.start`` (carries cwd) + a message."""
    start = {
        "type": "session.start",
        "data": {"sessionId": "cop", "context": {"cwd": cwd}},
        "id": "cop-start",
        "timestamp": "2026-06-29T17:47:20.282Z",
    }
    user = {
        "type": "user.message",
        "data": {"content": content, "attachments": []},
        "id": "cop-user-1",
        "timestamp": "2026-06-29T17:47:21.000Z",
    }
    dest.write_text(json.dumps(start) + "\n" + json.dumps(user) + "\n", encoding="utf-8")


def _write_claude_session(dest: Path, *, content: str) -> None:
    """A minimal Claude wire-format session log: one user message carrying the fact."""
    user = {"type": "user", "message": {"content": content}}
    dest.write_text(json.dumps(user) + "\n", encoding="utf-8")


def _make_config(tmp_path: Path):
    graph_path = tmp_path / "graph.db"
    return load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "kuzu"},
    )


async def _drain(ingester, *, timeout: float = 120.0) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(ingester.run(stop))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while ingester.stats()["spool_pending"] > 0 and loop.time() < deadline:
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=timeout)


@pytest.mark.integration
def test_copilot_and_claude_unify_in_one_namespace_graph(
    tmp_path, gate_embedder, mock_llm_factory
) -> None:
    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        repo = _make_repo(tmp_path / "widgets")

        copilot_events = tmp_path / "copilot_events.jsonl"
        claude_log = tmp_path / "claude_session.jsonl"
        _write_copilot_session(copilot_events, cwd=str(repo), content=COPILOT_FACT)
        _write_claude_session(claude_log, content=CLAUDE_FACT)

        spool_path = cfg.home_path / "spool" / "spool.db"

        # --- observe BOTH agents into the SAME spool via their OWN providers ---------
        spool = Spool(spool_path)
        try:
            # Copilot: namespace DERIVED from its own session.start cwd (no cwd passed).
            cop_result = await run_observe(
                copilot_events,
                "cop-session",
                spool=spool,
                provider=CopilotProvider(),
                config=cfg,
            )
            # Claude: same real repo passed as cwd (resolve_session_cwd is Copilot-shaped);
            # the namespace is still DERIVED by resolve_context from that repo's git remote.
            claude_result = await run_observe(
                claude_log,
                "claude-session",
                spool=spool,
                provider=ClaudeCodeProvider(),
                config=cfg,
                cwd=str(repo),
            )

            # The unification proof: two different agents → the SAME derived namespace/repo,
            # not a hard-coded equal string.
            assert cop_result.namespace == EXPECTED_NAMESPACE
            assert claude_result.namespace == EXPECTED_NAMESPACE
            assert cop_result.namespace == claude_result.namespace
            assert cop_result.repo == claude_result.repo == EXPECTED_REPO
            assert cop_result.appended == 1
            assert claude_result.appended == 1
            assert spool.pending() == 2
        finally:
            spool.close()

        # --- drain BOTH into ONE engine, recall spans BOTH agents --------------------
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(VOCAB),
            embedder=gate_embedder,
        )
        try:
            ingester = default_ingester_factory(engine, cfg)
            assert ingester is not None
            await _drain(ingester)
            assert ingester.stats()["episodes_ingested"] == 2
            assert ingester.stats()["spool_pending"] == 0

            # Both agents' facts coexist in the ONE derived-namespace graph and are each
            # recallable from it — the headline cross-agent unification assertion.
            copilot_recall = _recall_blob(
                await engine.search(COPILOT_QUERY, namespace=EXPECTED_NAMESPACE)
            )
            claude_recall = _recall_blob(
                await engine.search(CLAUDE_QUERY, namespace=EXPECTED_NAMESPACE)
            )
            assert "zephyr" in copilot_recall, (
                f"missing Copilot-contributed fact: {copilot_recall!r}"
            )
            assert "quasar" in claude_recall, f"missing Claude-contributed fact: {claude_recall!r}"
        finally:
            await engine.close()

    asyncio.run(scenario())
