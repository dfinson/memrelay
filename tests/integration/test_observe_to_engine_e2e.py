"""Full-chain e2e: observe -> durable spool -> daemon ingester -> engine -> recall.

The end-to-end proof that Session C's observation half truly connects to the merged
ingest + engine halves. A synthetic Copilot session (the committed fixture's message
content is redacted, so we author one carrying a real, recallable fact) is driven
through the *real* observe path -- ``CopilotProvider`` adapter -> traceforge
``EventPipeline`` -> :class:`GraphitiSink` -- into session B's **real** durable
``Spool`` at the canonical ``<home>/spool/spool.db``. It is then drained by the
ingester built by Session A's own :func:`default_ingester_factory` (which
*independently* recomputes that same spool path) into a real embedded-Kuzu
:class:`MemoryEngine`, and the observed fact is recalled by a semantic
``engine.search`` under the namespace the observe side derived from the git remote.

Because the observe side and the daemon side resolve the spool path **independently**,
a ``spool/`` subdir or record-shape mismatch surfaces here as
``episodes_ingested == 0`` and an empty recall -- exactly the class of bug a
``FakeSpool`` test cannot catch. Fully hermetic: the deterministic mock LLM + the
real/offline embedder from ``conftest.py``, a temp git repo for namespace resolution,
and embedded Kuzu on ``tmp_path``; no network, no API key, never a real ``~/.memrelay``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from memrelay.config import load_config
from memrelay.daemon.runtime import default_ingester_factory
from memrelay.engine.graphiti import MemoryEngine
from memrelay.ingest.graphiti_sink import run_observe
from memrelay.ingest.spool import Spool

REMOTE_URL = "https://github.com/acme/widgets.git"
FACT = (
    "Team note for memrelay: memrelay stores all persistent agent memory in an embedded "
    "Kuzu graph database, and the Kuzu database file lives under the memrelay home directory."
)
RECALL_QUERY = "which graph database does memrelay use to store its persistent memory"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _make_repo(path: Path) -> Path:
    """A minimal git repo whose ``origin`` remote drives namespace resolution."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    _git("remote", "add", "origin", REMOTE_URL, cwd=path)
    return path


def _write_session(dest: Path, *, cwd: str, content: str) -> None:
    """A minimal raw Copilot ``events.jsonl``: a ``session.start`` + one user message."""
    start = {
        "type": "session.start",
        "data": {"sessionId": "syn", "context": {"cwd": cwd}},
        "id": "syn-start",
        "timestamp": "2026-06-29T17:47:20.282Z",
    }
    user = {
        "type": "user.message",
        "data": {"content": content, "attachments": []},
        "id": "syn-user-1",
        "timestamp": "2026-06-29T17:47:21.000Z",
    }
    dest.write_text(json.dumps(start) + "\n" + json.dumps(user) + "\n", encoding="utf-8")


def _make_config(tmp_path: Path):
    """Hermetic config: temp home + embedded Kuzu graph, isolated from the real env."""
    graph_path = tmp_path / "graph.db"
    return load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "kuzu"},
    )


async def _drain(ingester, *, timeout: float = 120.0) -> None:
    """Run the daemon's ingester until the spool is fully consumed, then stop it."""
    stop = asyncio.Event()
    task = asyncio.create_task(ingester.run(stop))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while ingester.stats()["spool_pending"] > 0 and loop.time() < deadline:
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=timeout)


@pytest.mark.integration
def test_observe_to_spool_to_ingester_to_recall(tmp_path, gate_embedder, mock_llm_factory) -> None:
    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        repo = _make_repo(tmp_path / "widgets")
        events = tmp_path / "events.jsonl"
        _write_session(events, cwd=str(repo), content=FACT)

        # Canonical spool path — MUST equal A's daemon: config.home_path/"spool"/"spool.db".
        spool_path = cfg.home_path / "spool" / "spool.db"

        # --- observe side: drive the REAL pipeline into the REAL spool (no fakes) ---
        observe_spool = Spool(spool_path)
        try:
            result = await run_observe(events, "obs-session", spool=observe_spool, config=cfg)
            # Namespace/repo are derived from the git remote via mcp.namespace — the exact
            # function recall uses, so the episode is stored where recall will look.
            assert result.namespace == "acme"
            assert result.repo == "acme/widgets"
            assert result.appended == 1
            assert observe_spool.pending() == 1
        finally:
            observe_spool.close()  # release the WAL lock before the ingester reopens it

        # --- daemon side: A's OWN factory recomputes the path + drains into the engine ---
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["memrelay", "Kuzu"]),
            embedder=gate_embedder,
        )
        try:
            ingester = default_ingester_factory(engine, cfg)
            assert ingester is not None, "ingest seams should be merged on main"

            await _drain(ingester)

            # If observe wrote a *different* path than the daemon reads, this is 0 — the
            # silent-no-recall bug a FakeSpool can't detect.
            assert ingester.stats()["episodes_ingested"] == 1
            assert ingester.stats()["spool_pending"] == 0

            # The observed fact is recallable under the observe-derived namespace.
            results = await engine.search(RECALL_QUERY, namespace="acme")
            assert set(results) == {"nodes", "edges", "scores"}, f"bad shape: {results!r}"
            assert results["nodes"], "recall returned no nodes after draining the spool"
            blob = " ".join(
                f"{node.get('name') or ''} {node.get('summary') or ''}" for node in results["nodes"]
            )
            blob += " " + " ".join(
                f"{edge.get('name') or ''} {edge.get('fact') or ''}" for edge in results["edges"]
            )
            assert "kuzu" in blob.lower(), f"expected the observed fact to be recalled: {results!r}"
        finally:
            await engine.close()

    asyncio.run(scenario())
