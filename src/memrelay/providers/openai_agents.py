"""OpenAI Agents SDK framework provider — live-source, opt-in (SPEC §2.1, E12-S6 #72).

The OpenAI Agents SDK records a run as a ``trace`` plus nested ``trace.span`` objects
(function spans, generation spans, …) whose ``export()`` fires at span-close. A trace
processor batches them and serves the accumulated export, so memrelay **polls** it
(``HttpPollSource``). The installed traceforge ``openai_agents.yaml`` declares
``preprocessor: openai_agents`` (auto-applied inside ``MappedJsonAdapter.parse``), which
flattens the nested ``span_data`` and splits a function span into its call/output pair — so
:meth:`make_adapter` is still the single ``from_yaml`` one-liner the base provides.

Opt-in via ``MEMRELAY_OPENAI_AGENTS_ENDPOINT``; ingest-only (see :class:`LiveSourceProvider`).
"""

from __future__ import annotations

from memrelay.providers._live_source import TRANSPORT_HTTP_POLL, LiveSourceProvider
from memrelay.providers.registry import register


@register
class OpenAIAgentsProvider(LiveSourceProvider):
    """:class:`~memrelay.providers._live_source.LiveSourceProvider` for the OpenAI Agents SDK."""

    id = "openai_agents"
    endpoint_env = "MEMRELAY_OPENAI_AGENTS_ENDPOINT"
    transport = TRANSPORT_HTTP_POLL
    MAPPING = "openai_agents.yaml"
