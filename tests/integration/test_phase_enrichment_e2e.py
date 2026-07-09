"""End-to-end proof that a derived workflow phase flows observe -> spool -> ingest -> graph.

Two layers, both hermetic:

* :func:`test_phase_flows_observe_to_spool_to_ingest_to_recall` drives the **real**
  observe path (``run_observe`` -> traceforge ``EventPipeline`` -> real
  :class:`GraphitiSink`) into session B's **real** durable ``Spool``, then drains it with
  Session A's **real** ingester into a **real** embedded-Ladybug :class:`MemoryEngine` and
  recalls the fact -- with a deterministic **fake** phase inferencer injected via
  ``run_observe(phase_resolver=...)`` so no ML model is loaded. It proves the *wiring*:
  the derived phase rides the spool as a structured field (F2) and is folded into the
  noted content (F3), so a word that appears **nowhere in the source session** ("the
  phase label") is nonetheless recalled from the graph. A broken seam anywhere in that
  chain makes the phase word absent from recall -- a failure a ``FakeSpool`` cannot catch.

* :func:`test_real_phase_model_labels_land_in_graph` is the opt-in CI-only twin: it uses
  the **real** ``resolve_phase`` + traceforge phase model (guarded by ``importorskip`` so
  it skips wherever the optional ``phase`` deps are absent) to prove a genuine model label
  lands and is retrievable. Offline-forced; no network, no API key.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from traceforge.classify.workflow import Phase

from memrelay.config import load_config
from memrelay.daemon.runtime import default_ingester_factory
from memrelay.engine.graphiti import MemoryEngine
from memrelay.ingest.graphiti_sink import run_observe
from memrelay.ingest.spool import Spool

REMOTE_URL = "https://github.com/acme/widgets.git"
#: A fact that contains NONE of the four phase labels, so the only way a phase word can
#: reach the graph is via the ingester folding the derived phase into the noted content.
FACT = (
    "The widgets nightly deployment runbook lives on the internal operations wiki page "
    "and is owned by the platform on-call rotation."
)
RECALL_QUERY = "where does the widgets deployment runbook live"
PHASE_LABEL = "implementation"
PHASES: tuple[str, ...] = ("planning", "implementation", "verification", "exploration")


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


def _make_config(tmp_path: Path, *, enable_phase: bool = False):
    """Hermetic config: temp home + embedded Ladybug graph, isolated from the real env."""
    graph_path = tmp_path / "graph.db"
    return load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "ladybug"},
        ingest={"enable_phase": enable_phase},
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


class _FakePhaseStream:
    """A per-session stream matching traceforge's ``push``/``flush`` contract.

    ``EventPipeline._emit`` calls ``new_stream(session_id, source)`` once per session,
    then ``push(event) -> list[SessionEvent]`` per event (each already phase-stamped) and
    ``flush() -> list[SessionEvent]`` at end. This fake stamps every event with a fixed
    label and holds nothing back -- deterministic, no model, no network.
    """

    def __init__(self, label: str) -> None:
        self._label = label

    def push(self, event):
        meta = getattr(event, "metadata", None)
        if meta is None:
            return [event]
        stamped = meta.model_copy(update={"phase": Phase(self._label)})
        return [event.model_copy(update={"metadata": stamped})]

    def flush(self):
        return []


class _FakeInferencer:
    """Deterministic stand-in for ``traceforge.phase.inferencer.PhaseInferencer``."""

    def __init__(self, label: str) -> None:
        self._label = label

    def new_stream(self, session_id, source):
        return _FakePhaseStream(self._label)


def _recall_blob(results: dict) -> str:
    """Flatten a recall result's node/edge free-text into one lowercased string."""
    blob = " ".join(
        f"{node.get('name') or ''} {node.get('summary') or ''}" for node in results["nodes"]
    )
    blob += " " + " ".join(
        f"{edge.get('name') or ''} {edge.get('fact') or ''}" for edge in results["edges"]
    )
    return blob.lower()


@pytest.mark.integration
def test_phase_flows_observe_to_spool_to_ingest_to_recall(
    tmp_path, gate_embedder, mock_llm_factory
) -> None:
    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        repo = _make_repo(tmp_path / "widgets")
        events = tmp_path / "events.jsonl"
        _write_session(events, cwd=str(repo), content=FACT)

        spool_path = cfg.home_path / "spool" / "spool.db"

        # --- observe side: REAL pipeline + REAL spool, with a FAKE phase inferencer ---
        observe_spool = Spool(spool_path)
        try:
            result = await run_observe(
                events,
                "obs-session",
                spool=observe_spool,
                config=cfg,
                phase_resolver=lambda _cfg: (True, _FakeInferencer(PHASE_LABEL)),
            )
            assert result.namespace == "acme"
            assert result.repo == "acme/widgets"
            assert result.appended == 1
            assert observe_spool.pending() == 1

            # F2 proof: the derived phase crossed the wire as a STRUCTURED sidecar field
            # (not smuggled into content -- the composed content is still the raw fact).
            seq, record = observe_spool.read_batch(10)[0]
            assert record["phase"] == PHASE_LABEL, "derived phase must ride the spool record"
            assert PHASE_LABEL not in record["content"].lower(), (
                "phase is a sidecar field at the spool, folded into content only at ingest"
            )
        finally:
            observe_spool.close()  # release the WAL lock before the ingester reopens it

        # --- daemon side: A's OWN factory recomputes the path + drains into the engine ---
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["runbook", PHASE_LABEL]),
            embedder=gate_embedder,
        )
        try:
            ingester = default_ingester_factory(engine, cfg)
            assert ingester is not None, "ingest seams should be merged on main"

            await _drain(ingester)
            assert ingester.stats()["episodes_ingested"] == 1
            assert ingester.stats()["spool_pending"] == 0

            # F3 proof: the phase word -- which appears NOWHERE in the source session --
            # is recalled, because the ingester folded ``Phase: implementation`` into the
            # noted content, which the graph then indexed. If any seam dropped the phase,
            # this word is absent and the assertion fails.
            results = await engine.search(RECALL_QUERY, namespace="acme")
            assert set(results) == {"nodes", "edges", "scores"}, f"bad shape: {results!r}"
            assert results["nodes"], "recall returned no nodes after draining the spool"
            blob = _recall_blob(results)
            assert PHASE_LABEL in blob, f"derived phase not recalled from graph: {results!r}"
        finally:
            await engine.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_real_phase_model_labels_land_in_graph(
    tmp_path, gate_embedder, mock_llm_factory, monkeypatch
) -> None:
    """Opt-in twin: the REAL traceforge phase model produces a label that reaches recall.

    Skips wherever the optional ``phase`` deps are absent (``pip install memrelay[phase]``
    provides them in CI). Forced offline: the phase embedder is vendored, so a real label
    flows with zero network.
    """
    pytest.importorskip("requests", reason="phase extra absent (pip install memrelay[phase])")
    pytest.importorskip("model2vec", reason="phase extra absent")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    async def scenario() -> None:
        cfg = _make_config(tmp_path, enable_phase=True)
        repo = _make_repo(tmp_path / "widgets")
        events = tmp_path / "events.jsonl"
        _write_session(events, cwd=str(repo), content=FACT)

        spool_path = cfg.home_path / "spool" / "spool.db"
        observe_spool = Spool(spool_path)
        try:
            # No phase_resolver override -> the REAL resolve_phase + real model run.
            result = await run_observe(events, "obs-session", spool=observe_spool, config=cfg)
            assert result.appended == 1
            assert observe_spool.pending() == 1
            seq, record = observe_spool.read_batch(10)[0]
            assert record["phase"] in PHASES, f"real model produced no valid phase: {record!r}"
            real_label = record["phase"]
        finally:
            observe_spool.close()

        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["runbook", *PHASES]),
            embedder=gate_embedder,
        )
        try:
            ingester = default_ingester_factory(engine, cfg)
            await _drain(ingester)
            assert ingester.stats()["episodes_ingested"] == 1

            results = await engine.search(RECALL_QUERY, namespace="acme")
            assert results["nodes"], "recall returned no nodes after draining the spool"
            blob = _recall_blob(results)
            assert real_label in blob, f"real phase '{real_label}' not recalled: {results!r}"
        finally:
            await engine.close()

    asyncio.run(scenario())
