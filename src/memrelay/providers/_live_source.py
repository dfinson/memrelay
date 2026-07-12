"""Shared base for the E12-S6 live-source *framework* providers (SPEC §2.1, #72).

The twelve providers shipped so far read an agent's on-disk session **files** and
auto-detect by scanning the filesystem. The six agents added in E12-S6 — CrewAI,
LangGraph, MAF (Microsoft Agent Framework), OpenAI Agents, Pydantic AI, smolagents — are
**framework runtimes**, not CLIs: they emit events at *runtime* over an HTTP endpoint or an
SSE stream, so there is no on-disk trace to scan. They are therefore **live-source,
opt-in** providers, and everything they share lives here so each concrete provider is a
~15-line declaration of four class attributes.

Three design points make them safe to add to the auto-detected registry:

1. **Opt-in detection (never auto-hijack).** :meth:`LiveSourceProvider.is_present` returns
   True *only* when this framework's live endpoint is explicitly configured via its
   ``MEMRELAY_<FRAMEWORK>_ENDPOINT`` env var (mirroring how ``copilot`` reads
   ``MEMRELAY_COPILOT_HOME``). Unconfigured → False, cheaply, without raising or touching
   the network. So on a box that has not opted in, none of these six can win
   :meth:`~memrelay.providers.registry.ProviderRegistry.resolve`, and ``memrelay``'s
   status/init/ingest behavior is byte-identical to before.

2. **Dual-mode source (hermetic conformance + real live intake).** :meth:`make_source`
   with an explicit ``path=`` returns a tiny **synchronous** :class:`_LiveReplaySource`
   over that fixture — exactly what every file provider returns, and exactly what the
   registry-driven conformance harness
   (``tests/integration/test_agent_conformance.py``) iterates *synchronously* feeding each
   line to ``adapter.parse``. Without a ``path`` it builds the real, **asynchronous**
   traceforge ``HttpPollSource``/``SSESource`` from the opt-in endpoint. (traceforge's own
   ``ReplaySource`` is async and yields ``RawRecord`` objects, so it cannot back the sync
   replay branch — hence the local sync source, the same choice the file providers make.)

3. **Ingest-only (honest primitive).** These frameworks are ingested *from*, not served
   *to*: memrelay does not run inside their process, so the three serving hooks
   (:attr:`mcp_config_path`, :meth:`mcp_server_entry`, :meth:`register`) raise
   ``NotImplementedError`` with a clear message — exactly like the ingest-only CLI
   providers (``codex`` et al.). The merged ``cli.py`` guard (#71) already wraps the single
   ``register()`` call in ``try/except NotImplementedError`` so ``memrelay init`` still
   exits 0 when such a provider is (explicitly) resolved.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from memrelay.providers.base import AgentProvider, LLMStrategyHint, SessionRef

#: The two live transports these framework providers use (both are exercised across the
#: six: three poll an accumulating HTTP trace, three read a continuous event stream).
TRANSPORT_HTTP_POLL = "http_poll"
TRANSPORT_SSE = "sse"

#: Framework runtimes have no key-less host-borrow path in memrelay's engine, so they
#: advertise the honest bring-your-own-key default (metadata only; → ``config.llm.strategy``).
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path.

    traceforge's ``from_yaml`` wants a real path and ``traceforge.mappings`` has no
    name→path resolver, so we locate the file via ``importlib.resources`` (identical to the
    reference providers; every mapping ships inside the installed traceforge package).
    """
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class _LiveReplaySource:
    """A synchronous replay source: iterate a fixture's JSONL as raw string lines.

    Identical in shape to ``CopilotSource``/``CodexSource``: blank lines are skipped and
    each yielded line is a JSON object string ready for :meth:`make_adapter`'s ``parse``.
    This backs the ``path=`` branch of :meth:`LiveSourceProvider.make_source` so the
    hermetic conformance replay (which iterates the source *synchronously*) works for a
    framework provider exactly as it does for a file provider — no event loop, no network.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[str]:
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    yield stripped


class LiveSourceProvider(AgentProvider):
    """Base :class:`AgentProvider` for opt-in, live-source framework runtimes.

    A concrete provider sets only four class attributes and (for MAF) overrides
    :meth:`make_adapter`::

        @register
        class CrewaiProvider(LiveSourceProvider):
            id = "crewai"
            endpoint_env = "MEMRELAY_CREWAI_ENDPOINT"
            transport = TRANSPORT_HTTP_POLL
            MAPPING = "crewai.yaml"

    The base implements the whole frozen contract once. It is intentionally **not**
    decorated with ``@register`` (so the registry ignores it) and must import cleanly (the
    pkgutil sweep imports this module like any sibling).
    """

    #: ``MEMRELAY_<FRAMEWORK>_ENDPOINT`` — the opt-in env var carrying the live endpoint.
    endpoint_env: str
    #: ``TRANSPORT_HTTP_POLL`` or ``TRANSPORT_SSE`` — which traceforge source to build live.
    transport: str
    #: The packaged traceforge mapping YAML for this framework (unused when
    #: :meth:`make_adapter` is overridden, e.g. MAF's OTel-span adapter).
    MAPPING: str

    def __init__(self, endpoint: str | None = None) -> None:
        #: The live endpoint (HTTP-poll URL or SSE URL); ``None`` means "not opted in".
        self.endpoint = endpoint

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> LiveSourceProvider:
        """Build a provider, resolving its live endpoint.

        For these framework runtimes ``home`` is reinterpreted as an explicit **endpoint**
        override (the uniform registry constructor passes ``home``); ``None`` resolves
        ``$<endpoint_env>`` and otherwise stays ``None`` (not opted in). Constructs cheaply
        with **no network** — detection is a pure env-var read.
        """
        import os

        if home is not None:
            endpoint: str | None = str(home)
        else:
            endpoint = os.environ.get(cls.endpoint_env)
        return cls(endpoint)

    def is_present(self) -> bool:
        """True **only** when this framework's live endpoint is explicitly configured.

        Opt-in by construction: with no ``$<endpoint_env>`` set the provider is absent, so
        it never wins auto-detect and default ``memrelay`` behavior is unchanged. Cheap and
        never raises (a pure ``is not None`` check — no filesystem, no network).
        """
        return self.endpoint is not None

    # ── (1) source + mapping ─────────────────────────────────────────────────

    def make_source(self, session_id: str | None = None, *, path: str | Path | None = None) -> Any:
        """Return the source that reads this framework's trace (dual-mode).

        * ``path`` given → a synchronous :class:`_LiveReplaySource` over that fixture (the
          hermetic replay the conformance harness and unit tests drive).
        * otherwise → the live traceforge ``HttpPollSource``/``SSESource`` built from the
          opt-in endpoint (the production intake the daemon seam consumes).

        Raises :class:`ValueError` when neither a ``path`` nor an endpoint is available, so
        an un-opted-in production call fails loudly instead of silently doing nothing.
        """
        if path is not None:
            return _LiveReplaySource(path)
        if self.endpoint is None:
            raise ValueError(
                f"{self.id}: no live endpoint configured — set ${self.endpoint_env} "
                f"(opt-in) or pass path= to replay a recorded fixture"
            )
        return self._make_live_source()

    def _make_live_source(self) -> Any:
        """Build the live traceforge source for :attr:`transport` (endpoint is set)."""
        from traceforge.sources import HttpPollSource, SSESource

        if self.transport == TRANSPORT_HTTP_POLL:
            return HttpPollSource(url=self.endpoint, name=self.id)
        if self.transport == TRANSPORT_SSE:
            return SSESource(url=self.endpoint, name=self.id)
        raise ValueError(f"{self.id}: unknown transport {self.transport!r}")

    def make_adapter(self, session_id: str) -> Any:
        """Build this framework's traceforge adapter scoped to ``session_id``.

        The default is the single ``MappedJsonAdapter.from_yaml(<MAPPING>, session_id)``;
        the mapping's declared ``preprocessor`` (openai_agents/pydantic_ai/smolagents) runs
        inside ``MappedJsonAdapter.parse``. MAF overrides this to an ``OtelSpanAdapter``.
        """
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(self.MAPPING), session_id)

    def discover_sessions(self) -> Iterable[SessionRef]:
        """No on-disk sessions to enumerate: live intake is the daemon seam, not a scan.

        Returns empty so callers iterate harmlessly (honest and cheap — there is no
        filesystem trace for a framework runtime).
        """
        return ()

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Replay raw JSONL lines from ``ref.path`` when given; otherwise refuse.

        A framework runtime has no on-disk trace, so a ``ref`` without a ``path`` cannot be
        read here (live intake flows through :meth:`make_source`). A ``ref`` carrying an
        explicit fixture ``path`` (e.g. a recorded replay) is streamed like any file.
        """
        if not ref.path:
            raise NotImplementedError(
                f"{self.id} is a live-source provider: no on-disk trace to read — provide "
                f"ref.path to replay a recording, or use make_source() for the live endpoint"
            )
        yield from _LiveReplaySource(ref.path)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise the framework default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration — ingest-only ─────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        raise NotImplementedError(
            f"{self.id} is a live-source framework: ingest-only (memrelay ingests events "
            f"FROM its live endpoint; it does not serve MCP TO it)"
        )

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        raise NotImplementedError(
            f"{self.id} is a live-source framework: ingest-only — memrelay does not serve MCP to it"
        )

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        raise NotImplementedError(
            f"{self.id} is a live-source framework: ingest-only — memrelay does not register MCP"
        )
