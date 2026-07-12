"""smolagents framework provider — live-source, opt-in (SPEC §2.1, E12-S6 #72).

smolagents records discrete memory steps — a ``TaskStep`` (task), the system prompt, then an
``ActionStep`` per iteration (model output + ``tool_calls`` + observations), ``PlanningStep``s,
and a final answer. A step listener accumulates them at each step boundary and serves the
growing list, so memrelay **polls** it (``HttpPollSource``). The installed traceforge
``smolagents.yaml`` declares ``preprocessor: smolagents`` (auto-applied inside
``MappedJsonAdapter.parse``), which routes on field-presence and splits an action step's
``tool_calls`` — so :meth:`make_adapter` is the base's ``from_yaml`` one-liner.

Opt-in via ``MEMRELAY_SMOLAGENTS_ENDPOINT``; ingest-only (see :class:`LiveSourceProvider`).
"""

from __future__ import annotations

from memrelay.providers._live_source import TRANSPORT_HTTP_POLL, LiveSourceProvider
from memrelay.providers.registry import register


@register
class SmolagentsProvider(LiveSourceProvider):
    """:class:`~memrelay.providers._live_source.LiveSourceProvider` for smolagents."""

    id = "smolagents"
    endpoint_env = "MEMRELAY_SMOLAGENTS_ENDPOINT"
    transport = TRANSPORT_HTTP_POLL
    MAPPING = "smolagents.yaml"
