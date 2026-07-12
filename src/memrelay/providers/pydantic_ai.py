"""Pydantic AI framework provider — live-source, opt-in (SPEC §2.1, E12-S6 #72).

Pydantic AI streams a run as native events — ``agent_run_start``, model ``request`` parts,
``function_tool_call`` events, and streamed ``response`` parts (``part_start``/``part_delta``).
Because the events arrive as a live stream, memrelay reads them over **SSE** (``SSESource``).
The installed traceforge ``pydantic_ai.yaml`` declares ``preprocessor: pydantic_ai``
(auto-applied inside ``MappedJsonAdapter.parse``), which normalizes the ``kind`` /
``event_kind`` / ``part_kind`` shapes — so :meth:`make_adapter` is the base's ``from_yaml``
one-liner.

Opt-in via ``MEMRELAY_PYDANTIC_AI_ENDPOINT``; ingest-only (see :class:`LiveSourceProvider`).
"""

from __future__ import annotations

from memrelay.providers._live_source import TRANSPORT_SSE, LiveSourceProvider
from memrelay.providers.registry import register


@register
class PydanticAIProvider(LiveSourceProvider):
    """:class:`~memrelay.providers._live_source.LiveSourceProvider` for Pydantic AI."""

    id = "pydantic_ai"
    endpoint_env = "MEMRELAY_PYDANTIC_AI_ENDPOINT"
    transport = TRANSPORT_SSE
    MAPPING = "pydantic_ai.yaml"
