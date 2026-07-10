"""THE pre-release trust gate: a fixture SESSION is observed, ingested, and later
surfaced by the agent's ``memory_recall`` MCP tool against the real embedded graph (E11-S3 #19).

This is the single end-to-end flow that mirrors what a coding agent actually experiences in
production, and it deliberately composes the two halves that ship on ``main`` but which no other
test joins:

* ``test_observe_to_engine_e2e`` proves the *capture* half -- a raw Copilot ``events.jsonl`` ->
  ``run_observe`` -> durable :class:`Spool` -> :func:`default_ingester_factory` ingester -> real
  :class:`MemoryEngine` -- but recalls with a **direct** ``engine.search`` call, never touching the
  daemon socket or the MCP tools an agent sees.
* ``test_mcp_engine_roundtrip`` (#18) proves the *agent-facing recall* half -- ``memory_recall`` ->
  ``DaemonClient`` -> daemon socket -> real engine -> ``mcp.format`` renderer -- but seeds the graph
  with ``memory_note`` (an agent explicitly writing a fact), **not** an observed session.

The release gate is the join: a fixture **session** is driven through the real observe -> spool ->
ingest path into the real engine, and then the ingested memory is recalled **through the daemon +
MCP ``memory_recall`` tool surface** -- the exact seam the agent calls. A regression anywhere along
capture, spooling, ingestion, namespace derivation, the daemon transport, or the map renderer breaks
this one test, which is why it is the trust gate a human runs before cutting a release.

Fully hermetic and keyless -- that IS the "runs headless" acceptance criterion. The deterministic
in-process mock LLM + the real/offline embedder from ``conftest.py`` stand in for extraction and
embeddings, namespace is derived from a throwaway git repo's remote, and the graph is embedded
Ladybug on ``tmp_path``: no network, no API key, no external database, never a real ``~/.memrelay``.
See ``docs/release-gate.md`` for how to run it.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from memrelay.config import load_config
from memrelay.daemon.runtime import default_ingester_factory
from memrelay.daemon.server import DaemonServer
from memrelay.daemon.transport import resolve_endpoint
from memrelay.engine.graphiti import MemoryEngine
from memrelay.ingest.graphiti_sink import run_observe
from memrelay.ingest.spool import Spool
from memrelay.mcp.client import DaemonClient
from memrelay.mcp.server import build_mcp_server

REMOTE_URL = "https://github.com/acme/widgets.git"
EXPECTED_NAMESPACE = "acme"
EXPECTED_REPO = "acme/widgets"

#: The fixture session's recallable fact. ``Larkspur`` is a distinctive, semantically isolated
#: anchor (the Zephyr/Quasar recipe from ``test_cross_agent_recall``) so the query resolves to it
#: deterministically under BOTH the real fastembed embedder and the offline hashing fallback.
FACT = (
    "memrelay's pre-release trust is verified by the Larkspur roundtrip gate, and memrelay "
    "records the Larkspur gate outcome in its embedded memory graph."
)
RECALL_QUERY = "Larkspur roundtrip gate that verifies memrelay pre-release trust"
#: Entities the deterministic mock LLM "extracts" from ``FACT``.
VOCAB = ["memrelay", "Larkspur"]


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _make_repo(path: Path) -> Path:
    """A minimal git repo whose ``origin`` remote drives namespace resolution."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    _git("remote", "add", "origin", REMOTE_URL, cwd=path)
    return path


def _write_session(dest: Path, *, cwd: str, content: str) -> None:
    """A minimal raw Copilot ``events.jsonl``: a ``session.start`` + one user message.

    The committed ``copilot_fixture`` is content-redacted, so -- like the observe e2e sibling --
    the gate authors its own session carrying a real, recallable fact.
    """
    start = {
        "type": "session.start",
        "data": {"sessionId": "gate", "context": {"cwd": cwd}},
        "id": "gate-start",
        "timestamp": "2026-06-29T17:47:20.282Z",
    }
    user = {
        "type": "user.message",
        "data": {"content": content, "attachments": []},
        "id": "gate-user-1",
        "timestamp": "2026-06-29T17:47:21.000Z",
    }
    dest.write_text(json.dumps(start) + "\n" + json.dumps(user) + "\n", encoding="utf-8")


def _make_config(tmp_path: Path):
    """Hermetic config: temp home + embedded Ladybug graph, isolated from the real env."""
    graph_path = tmp_path / "graph.db"
    return load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "ladybug"},
    )


def _tool_text(result: object) -> str:
    """Extract the text of a FastMCP ``call_tool`` result (tuple or block list)."""
    blocks = result[0] if isinstance(result, tuple) else result
    return blocks[0].text


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
def test_release_gate_fixture_session_recalled_through_mcp(
    tmp_path: Path, gate_embedder, mock_llm_factory
) -> None:
    """Observe a fixture session, ingest it, and recall it through the daemon + MCP tools.

    The gate: the ingested SESSION fact is returned by the agent-facing ``memory_recall`` tool
    (rendered map, real engine -- the ``StubBackend`` sentinels are asserted *absent*), health over
    the same socket reports the real embedded Ladybug backend, and the fact is scoped to the
    observe-derived namespace (a foreign namespace sees nothing).
    """

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        repo = _make_repo(tmp_path / "widgets")
        events = tmp_path / "events.jsonl"
        _write_session(events, cwd=str(repo), content=FACT)

        # Canonical spool path -- MUST equal the daemon's: config.home_path/"spool"/"spool.db".
        spool_path = cfg.home_path / "spool" / "spool.db"

        # --- capture: drive the REAL observe pipeline for the fixture session into the spool ---
        spool = Spool(spool_path)
        try:
            result = await run_observe(events, "gate-session", spool=spool, config=cfg)
            # Namespace/repo are DERIVED from the session's git remote via mcp.namespace -- the
            # exact resolution recall uses, so the episode lands where recall will look.
            assert result.namespace == EXPECTED_NAMESPACE
            assert result.repo == EXPECTED_REPO
            assert result.appended == 1
            assert spool.pending() == 1
        finally:
            spool.close()  # release the WAL lock before the ingester reopens it
        namespace, repo_slug = result.namespace, result.repo

        # --- ingest: the daemon's OWN factory recomputes the path + drains into the real engine ---
        engine = await MemoryEngine.from_config(
            cfg, llm_client=mock_llm_factory(VOCAB), embedder=gate_embedder
        )
        try:
            ingester = default_ingester_factory(engine, cfg)
            assert ingester is not None, "ingest seams should be merged on main"
            await _drain(ingester)
            assert ingester.stats()["episodes_ingested"] == 1
            assert ingester.stats()["spool_pending"] == 0

            # --- recall THROUGH the agent-facing daemon + MCP tool surface (the release gate) ---
            endpoint = resolve_endpoint(tmp_path)
            daemon = DaemonServer(engine, endpoint)
            await daemon.start()
            try:
                client = DaemonClient(endpoint, timeout=10.0)
                # The MCP context resolver is fed the OBSERVE-derived namespace, proving recall
                # reads exactly where the observed session was written.
                mcp = build_mcp_server(client, context_resolver=lambda: (namespace, repo_slug))

                recall = _tool_text(await mcp.call_tool("memory_recall", {"query": RECALL_QUERY}))
                assert "## Memory Map" in recall, f"recall was not the rendered map:\n{recall}"
                assert "larkspur" in recall.lower(), (
                    f"ingested fixture-session fact not recalled through MCP:\n{recall}"
                )
                assert "stub-node-1" not in recall and "stub result for" not in recall, (
                    f"served the StubBackend, not the real engine:\n{recall}"
                )

                # Health over the same socket proves the REAL embedded Ladybug graph answered --
                # the stub's constant {"status": "running", …} can never satisfy this.
                health = await client.health()
                assert health["status"] == "ok", f"real-engine health not ok: {health!r}"
                assert health["backend"] == "ladybug", f"unexpected backend: {health!r}"

                # False-positive guard: a foreign namespace must NOT surface the ingested fact,
                # proving recall is scoped to the observe-derived namespace, not a global leak.
                intruder = build_mcp_server(
                    client, context_resolver=lambda: ("intruder", "intruder/repo")
                )
                leaked = _tool_text(
                    await intruder.call_tool("memory_recall", {"query": RECALL_QUERY})
                )
                assert leaked == "No relevant memories found.", (
                    f"the observed session leaked across namespaces:\n{leaked}"
                )
            finally:
                await daemon.stop()
        finally:
            # We built the engine (injected as the daemon's backend), so we release the Ladybug
            # lock here -- the daemon must not close an engine it does not own.
            await engine.close()

    asyncio.run(scenario())
