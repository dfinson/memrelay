"""CrewAI framework provider — live-source, opt-in (SPEC §2.1, E12-S6 #72).

CrewAI is a multi-agent *framework*, not a CLI: its event bus emits typed lifecycle events
(``crew_kickoff_started``, ``agent_execution_started``, ``tool_usage_*``, ``task_completed``,
``crew_kickoff_completed``) at runtime. A thin listener buffers those events and exposes the
accumulated trace over HTTP, so memrelay **polls** it (``HttpPollSource``) — the batch grows
between polls and each poll returns the JSONL-shaped body. The installed traceforge
``crewai.yaml`` maps the flat events directly (no preprocessor).

Opt-in via ``MEMRELAY_CREWAI_ENDPOINT``; ingest-only (see :class:`LiveSourceProvider`).
"""

from __future__ import annotations

from memrelay.providers._live_source import TRANSPORT_HTTP_POLL, LiveSourceProvider
from memrelay.providers.registry import register


@register
class CrewaiProvider(LiveSourceProvider):
    """:class:`~memrelay.providers._live_source.LiveSourceProvider` for CrewAI."""

    id = "crewai"
    endpoint_env = "MEMRELAY_CREWAI_ENDPOINT"
    transport = TRANSPORT_HTTP_POLL
    MAPPING = "crewai.yaml"
