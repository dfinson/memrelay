"""MAF (Microsoft Agent Framework) provider — live-source, opt-in (SPEC §2.1, E12-S6 #72).

MAF is instrumented with OpenTelemetry: it emits OTel **spans** (``agents.app.run``,
``agents.adapter.process``, ``agents.storage.read``, ``agents.app.after_turn``, …) over an
OTLP span stream. memrelay reads that continuous stream over **SSE** (``SSESource``).

Unlike the other five, MAF's traceforge mapping (``maf.yaml``) is a ``spans:`` map consumed by
traceforge's ``OtelSpanAdapter`` (which keys spans by ``name`` → canonical kinds), **not** the
``events:`` map ``MappedJsonAdapter`` reads. So this provider overrides :meth:`make_adapter` to
build the OTel-span adapter; ``MAPPING`` is retained only to document provenance.

Opt-in via ``MEMRELAY_MAF_ENDPOINT``; ingest-only (see :class:`LiveSourceProvider`).
"""

from __future__ import annotations

from typing import Any

from memrelay.providers._live_source import TRANSPORT_SSE, LiveSourceProvider
from memrelay.providers.registry import register


@register
class MafProvider(LiveSourceProvider):
    """:class:`~memrelay.providers._live_source.LiveSourceProvider` for MAF."""

    id = "maf"
    endpoint_env = "MEMRELAY_MAF_ENDPOINT"
    transport = TRANSPORT_SSE
    #: Provenance only — MAF uses the OTel-span adapter below, not ``MappedJsonAdapter``.
    MAPPING = "maf.yaml"

    def make_adapter(self, session_id: str) -> Any:
        """Build traceforge's ``OtelSpanAdapter`` (MAF emits OTel spans, not flat events)."""
        from traceforge.adapters.otel import OtelSpanAdapter

        return OtelSpanAdapter("stream", session_id)
