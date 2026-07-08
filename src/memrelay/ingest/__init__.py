"""Ingestion: normalize agent traces to ``SessionEvent`` and run the pipeline.

E0 provides a console sink and a fixture/session replay runner (the walking
skeleton). The durable spool + ``GraphitiSink`` arrive in later epics.
"""

from __future__ import annotations

from memrelay.ingest.console_sink import ConsoleSink
from memrelay.ingest.fixture_runner import IngestResult, replay, replay_async

__all__ = ["ConsoleSink", "IngestResult", "replay", "replay_async"]
