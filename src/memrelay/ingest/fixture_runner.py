"""Replay a Copilot ``events.jsonl`` through traceforge into ``SessionEvent``s.

This is the E0 walking skeleton (SPEC §3.2–§3.4) with **no Graphiti**: a real (or
fixture) session file is normalized by the ``copilot.yaml`` adapter and pushed
through a lean ``EventPipeline`` to a :class:`ConsoleSink`.

The pipeline runs with ``enable_phase=False`` / ``enable_boundary=False`` (the ML
inferencers need extra deps and only stamp optional metadata — see delta #7) and
``governance=None`` (observation-only opt-out, SPEC §3.3).
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from memrelay.ingest.console_sink import ConsoleSink
from memrelay.providers.copilot import CopilotProvider


@dataclass
class IngestResult:
    """Outcome of a fixture/session replay."""

    session_id: str
    parsed: int = 0  # SessionEvents produced by the adapter
    delivered: int = 0  # SessionEvents that reached the sink (post-filtering)
    by_kind: Counter[str] = field(default_factory=Counter)
    by_visibility: Counter[str] = field(default_factory=Counter)
    elapsed_s: float = 0.0


async def replay_async(
    events_path: str | Path,
    session_id: str,
    *,
    echo: bool = True,
    writer=print,
) -> IngestResult:
    """Normalize + push a session's events; return an :class:`IngestResult`."""
    from traceforge import Enricher, EventPipeline

    provider = CopilotProvider()
    adapter = provider.make_adapter(session_id)
    sink = ConsoleSink(writer=writer, echo=echo)
    pipeline = EventPipeline(
        sinks=[sink],
        enricher=Enricher(),
        governance=None,
        enable_phase=False,
        enable_boundary=False,
    )

    result = IngestResult(session_id=session_id)
    start = time.perf_counter()
    path = Path(events_path)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            for event in adapter.parse(line):
                result.parsed += 1
                await pipeline.push(event)

    await pipeline.flush()
    await pipeline.close()
    result.elapsed_s = time.perf_counter() - start

    result.delivered = sink.total
    result.by_kind = sink.by_kind
    result.by_visibility = sink.by_visibility
    return result


def replay(
    events_path: str | Path,
    session_id: str,
    *,
    echo: bool = True,
    writer=print,
) -> IngestResult:
    """Synchronous wrapper around :func:`replay_async`."""
    return asyncio.run(replay_async(events_path, session_id, echo=echo, writer=writer))
