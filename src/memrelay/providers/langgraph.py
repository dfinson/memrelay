"""LangGraph framework provider — live-source, opt-in (SPEC §2.1, E12-S6 #72).

LangGraph exposes execution as a live event stream: ``graph.astream_events(version="v2")``
yields ``on_chain_start`` / ``on_chat_model_start`` / ``on_chat_model_end`` /
``on_tool_start`` / ``on_tool_end`` / ``on_chain_end`` as they happen. That maps naturally to
**SSE** (``SSESource``) — one JSON event per server-sent message. The installed traceforge
``langgraph.yaml`` maps the flat events directly (no preprocessor).

Opt-in via ``MEMRELAY_LANGGRAPH_ENDPOINT``; ingest-only (see :class:`LiveSourceProvider`).
"""

from __future__ import annotations

from memrelay.providers._live_source import TRANSPORT_SSE, LiveSourceProvider
from memrelay.providers.registry import register


@register
class LangGraphProvider(LiveSourceProvider):
    """:class:`~memrelay.providers._live_source.LiveSourceProvider` for LangGraph."""

    id = "langgraph"
    endpoint_env = "MEMRELAY_LANGGRAPH_ENDPOINT"
    transport = TRANSPORT_SSE
    MAPPING = "langgraph.yaml"
