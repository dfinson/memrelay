"""Replay a Copilot ``events.jsonl`` through traceforge into ``SessionEvent``s.

This is the E0 walking skeleton (SPEC §3.2–§3.4) with **no Graphiti**: a real (or
fixture) session file is normalized by the ``copilot.yaml`` adapter and pushed
through a lean ``EventPipeline`` to a :class:`ConsoleSink`.

The pipeline runs with ``enable_phase`` / ``enable_boundary`` taken from
:class:`~memrelay.config.IngestConfig` (both default **False** — the ML
inferencers need extra deps and only stamp optional metadata, see delta #7) and
``governance=None`` (observation-only opt-out, SPEC §3.3).
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from memrelay.ingest.console_sink import ConsoleSink

if TYPE_CHECKING:
    from memrelay.config import Config


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
    config: Config | None = None,
    enable_phase: bool | None = None,
    enable_boundary: bool | None = None,
) -> IngestResult:
    """Normalize + push a session's events; return an :class:`IngestResult`.

    The ML inferencer flags come from :class:`~memrelay.config.IngestConfig`
    (default off, see delta #7); pass ``enable_phase`` / ``enable_boundary``
    explicitly to override the resolved config for a single run.
    """
    from traceforge import Enricher, EventPipeline

    from memrelay.config import load_config
    from memrelay.providers.registry import DEFAULT_PROVIDER_ID, get_registry

    cfg = config if config is not None else load_config()
    phase = cfg.ingest.enable_phase if enable_phase is None else enable_phase
    boundary = cfg.ingest.enable_boundary if enable_boundary is None else enable_boundary

    provider = get_registry().create(DEFAULT_PROVIDER_ID)
    adapter = provider.make_adapter(session_id)
    sink = ConsoleSink(writer=writer, echo=echo)
    pipeline = EventPipeline(
        sinks=[sink],
        enricher=Enricher(),
        governance=None,
        enable_phase=phase,
        enable_boundary=boundary,
    )

    result = IngestResult(session_id=session_id)
    start = time.perf_counter()
    path = Path(events_path)
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                for event in adapter.parse(line):
                    result.parsed += 1
                    await pipeline.push(event)
        await pipeline.flush()
    finally:
        # Always release the pipeline, even if push/flush raised — this is the
        # pattern GraphitiSink (later epic) will depend on to flush its buffer
        # and close the graph connection. flush() stays before close() on the
        # success path; on error we still close.
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
    config: Config | None = None,
    enable_phase: bool | None = None,
    enable_boundary: bool | None = None,
) -> IngestResult:
    """Synchronous wrapper around :func:`replay_async`."""
    return asyncio.run(
        replay_async(
            events_path,
            session_id,
            echo=echo,
            writer=writer,
            config=config,
            enable_phase=enable_phase,
            enable_boundary=enable_boundary,
        )
    )
