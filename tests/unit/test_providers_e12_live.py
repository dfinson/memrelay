"""Per-agent unit tests for the E12-S6 live-source *framework* providers (#72).

The six agents added in E12-S6 — CrewAI, LangGraph, MAF, OpenAI Agents, Pydantic AI,
smolagents — are **framework runtimes**, not CLIs: they emit events at runtime over an
HTTP-poll or SSE endpoint (no on-disk trace to scan). They therefore share
:class:`~memrelay.providers._live_source.LiveSourceProvider`, which makes them **opt-in**
(present only when their ``MEMRELAY_<FRAMEWORK>_ENDPOINT`` env var is set), **dual-mode**
(sync fixture replay for conformance vs. the live async traceforge source in production),
and **ingest-only** (the three MCP-serving hooks refuse).

These tests add the per-agent specifics the registry-driven conformance matrix
(``tests/integration/test_agent_conformance.py``) intentionally does not encode, and — most
importantly — prove the **byte-identical default-off** invariant: with no framework endpoint
configured, none of the six is ever auto-detected, so ``memrelay``'s resolution is unchanged.
No test here touches the network (the live transports are only *constructed*, never entered).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from memrelay.providers._live_source import (
    TRANSPORT_HTTP_POLL,
    TRANSPORT_SSE,
    LiveSourceProvider,
)
from memrelay.providers.base import AgentProvider, LLMStrategyHint, SessionRef
from memrelay.providers.crewai import CrewaiProvider
from memrelay.providers.langgraph import LangGraphProvider
from memrelay.providers.maf import MafProvider
from memrelay.providers.openai_agents import OpenAIAgentsProvider
from memrelay.providers.pydantic_ai import PydanticAIProvider
from memrelay.providers.registry import DEFAULT_PROVIDER_ID, ProviderRegistry, get_registry
from memrelay.providers.smolagents import SmolagentsProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@dataclass(frozen=True)
class Spec:
    """Everything the parametrized tests need to know about one framework provider."""

    id: str
    provider_cls: type[LiveSourceProvider]
    endpoint_env: str
    transport: str
    #: the traceforge source class name ``make_source()`` builds for the live (no-path) branch.
    source_cls_name: str
    #: the traceforge adapter class name ``make_adapter`` builds.
    adapter_cls_name: str
    #: the exact canonical event kinds the committed fixture replays to.
    expected_kinds: set[str]


SPECS: list[Spec] = [
    Spec(
        id="crewai",
        provider_cls=CrewaiProvider,
        endpoint_env="MEMRELAY_CREWAI_ENDPOINT",
        transport=TRANSPORT_HTTP_POLL,
        source_cls_name="HttpPollSource",
        adapter_cls_name="MappedJsonAdapter",
        expected_kinds={
            "session.started",
            "agent.spawned",
            "tool.call.started",
            "tool.call.completed",
            "task.completed",
            "session.ended",
        },
    ),
    Spec(
        id="langgraph",
        provider_cls=LangGraphProvider,
        endpoint_env="MEMRELAY_LANGGRAPH_ENDPOINT",
        transport=TRANSPORT_SSE,
        source_cls_name="SSESource",
        adapter_cls_name="MappedJsonAdapter",
        expected_kinds={
            "workflow.started",
            "llm.call.started",
            "llm.call.completed",
            "tool.call.started",
            "tool.call.completed",
            "workflow.completed",
        },
    ),
    Spec(
        id="maf",
        provider_cls=MafProvider,
        endpoint_env="MEMRELAY_MAF_ENDPOINT",
        transport=TRANSPORT_SSE,
        source_cls_name="SSESource",
        adapter_cls_name="OtelSpanAdapter",
        expected_kinds={
            "turn.started",
            "message.user",
            "memory.query.started",
            "hook.completed",
        },
    ),
    Spec(
        id="openai_agents",
        provider_cls=OpenAIAgentsProvider,
        endpoint_env="MEMRELAY_OPENAI_AGENTS_ENDPOINT",
        transport=TRANSPORT_HTTP_POLL,
        source_cls_name="HttpPollSource",
        adapter_cls_name="MappedJsonAdapter",
        expected_kinds={
            "session.started",
            "tool.call.started",
            "tool.call.completed",
            "llm.call.completed",
        },
    ),
    Spec(
        id="pydantic_ai",
        provider_cls=PydanticAIProvider,
        endpoint_env="MEMRELAY_PYDANTIC_AI_ENDPOINT",
        transport=TRANSPORT_SSE,
        source_cls_name="SSESource",
        adapter_cls_name="MappedJsonAdapter",
        expected_kinds={
            "session.started",
            "message.user",
            "tool.call.started",
            "llm.call.completed",
        },
    ),
    Spec(
        id="smolagents",
        provider_cls=SmolagentsProvider,
        endpoint_env="MEMRELAY_SMOLAGENTS_ENDPOINT",
        transport=TRANSPORT_HTTP_POLL,
        source_cls_name="HttpPollSource",
        adapter_cls_name="MappedJsonAdapter",
        expected_kinds={
            "session.started",
            "message.system",
            "message.assistant",
            "tool.call.started",
            "planning.started",
            "session.ended",
        },
    ),
]


def _id(spec: Spec) -> str:
    return spec.id


def _fixture(spec: Spec) -> Path:
    return FIXTURES / f"{spec.id}_session.jsonl"


@pytest.fixture(autouse=True)
def _clear_endpoint_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from any ambient ``MEMRELAY_<FRAMEWORK>_ENDPOINT`` opt-in."""
    for spec in SPECS:
        monkeypatch.delenv(spec.endpoint_env, raising=False)


# ── ABC conformance + self-registration ──────────────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_provider_satisfies_abc(spec: Spec) -> None:
    provider = spec.provider_cls()
    assert isinstance(provider, AgentProvider)
    assert isinstance(provider, LiveSourceProvider)


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_provider_self_registers_without_central_edit(spec: Spec) -> None:
    """The pkgutil sweep discovers each new module — no edit to any central list."""
    registry = get_registry()
    assert spec.id in registry.ids()
    assert isinstance(registry.create(spec.id), spec.provider_cls)


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_declared_class_attrs(spec: Spec) -> None:
    assert spec.provider_cls.endpoint_env == spec.endpoint_env
    assert spec.provider_cls.transport == spec.transport
    assert spec.provider_cls.transport in {TRANSPORT_HTTP_POLL, TRANSPORT_SSE}


# ── mapping + adapter + fixture replay ───────────────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_make_adapter_type(spec: Spec) -> None:
    """5 frameworks use ``MappedJsonAdapter``; MAF uses the OTel-span adapter."""
    assert type(spec.provider_cls().make_adapter("sid")).__name__ == spec.adapter_cls_name


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_make_adapter_scopes_to_session_id(spec: Spec) -> None:
    """Every event the fixture replays is stamped with the adapter's session id."""
    provider = spec.provider_cls()
    adapter = provider.make_adapter("sess-scope")
    events = [
        event for line in provider.make_source(path=_fixture(spec)) for event in adapter.parse(line)
    ]
    assert events, f"{spec.id} fixture produced no events"
    assert all(e.session_id == "sess-scope" for e in events)


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_fixture_replays_to_expected_canonical_kinds(spec: Spec) -> None:
    """The committed synthetic fixture yields exactly this framework's canonical kinds.

    Runs through the provider's own ``make_source`` + ``make_adapter`` (so the mapping's
    declared preprocessor / the OTel-span adapter is exercised), a stronger assertion than
    the conformance matrix's generic "≥3 canonical events" floor.
    """
    provider = spec.provider_cls()
    adapter = provider.make_adapter(f"fx-{spec.id}")
    kinds = {
        str(event.kind)
        for line in provider.make_source(path=_fixture(spec))
        for event in adapter.parse(line)
    }
    assert kinds == spec.expected_kinds


# ── dual-mode make_source (invariant A) ──────────────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_make_source_path_yields_sync_str_lines(spec: Spec) -> None:
    """The replay branch is a **synchronous** iterable of raw JSONL strings.

    This is exactly what the conformance harness iterates (``for line in
    provider.make_source(path=...)``) — traceforge's own async ``ReplaySource`` could not
    back it, hence the local ``_LiveReplaySource``.
    """
    source = spec.provider_cls().make_source(path=_fixture(spec))
    lines = list(source)  # sync iteration, no event loop
    assert lines and all(isinstance(line, str) for line in lines)
    raw = _fixture(spec).read_text(encoding="utf-8")
    expected = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    assert lines == expected


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_make_source_live_builds_declared_transport(spec: Spec) -> None:
    """Without a path, ``make_source`` builds the live traceforge source for its transport.

    The source is only *constructed* (url/name wired) — never entered — so no network.
    """
    provider = spec.provider_cls("http://framework.local/trace")
    source = provider.make_source()
    assert type(source).__name__ == spec.source_cls_name
    assert source.url == "http://framework.local/trace"
    assert source.name == spec.id


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_make_source_without_endpoint_or_path_raises(spec: Spec) -> None:
    """An un-opted-in production call fails loudly rather than silently no-op'ing."""
    with pytest.raises(ValueError, match="no live endpoint"):
        spec.provider_cls().make_source()


# ── discovery / read_raw (no on-disk trace) ──────────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_discover_sessions_is_empty(spec: Spec) -> None:
    assert list(spec.provider_cls().discover_sessions()) == []


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_read_raw_without_path_refuses(spec: Spec) -> None:
    ref = SessionRef(session_id="s", agent_id=spec.id, path=None)
    with pytest.raises(NotImplementedError):
        next(iter(spec.provider_cls().read_raw(ref)))


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_read_raw_with_path_replays_lines(spec: Spec) -> None:
    ref = SessionRef(session_id="s", agent_id=spec.id, path=str(_fixture(spec)))
    lines = list(spec.provider_cls().read_raw(ref))
    assert lines and all(isinstance(line, str) for line in lines)


# ── construction / detection: opt-in (invariant B) ───────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_from_home_honors_endpoint_env(spec: Spec, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(spec.endpoint_env, "http://env.local/trace")
    assert spec.provider_cls.from_home().endpoint == "http://env.local/trace"


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_from_home_explicit_overrides_env(spec: Spec, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(spec.endpoint_env, "http://env.local/trace")
    assert spec.provider_cls.from_home("http://explicit/trace").endpoint == "http://explicit/trace"


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_from_home_defaults_to_none_when_env_absent(spec: Spec) -> None:
    assert spec.provider_cls.from_home().endpoint is None


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_is_present_is_opt_in(spec: Spec) -> None:
    """Absent by default (no endpoint); present only once an endpoint is configured."""
    assert spec.provider_cls().is_present() is False
    assert spec.provider_cls("http://x/trace").is_present() is True
    assert spec.provider_cls.from_home("http://x/trace").is_present() is True


# ── LLM strategy advertisement (metadata only) ───────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_llm_strategy_is_byo_key(spec: Spec) -> None:
    assert spec.provider_cls().llm_strategy() == LLMStrategyHint(strategy="byo-key", host=None)


# ── ingest-only: the three serving hooks refuse (invariant C) ────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_ingest_only_serving_hooks_raise(spec: Spec) -> None:
    provider = spec.provider_cls()
    with pytest.raises(NotImplementedError):
        _ = provider.mcp_config_path
    with pytest.raises(NotImplementedError):
        provider.mcp_server_entry()
    with pytest.raises(NotImplementedError):
        provider.register()


# ── byte-identical default-off + opt-in flip (invariants B, C2) ──────────────


def test_frameworks_never_autodetect_by_default() -> None:
    """On the live registry, no framework is present unless its endpoint is configured.

    This is the byte-identical guarantee: with no ``MEMRELAY_<FRAMEWORK>_ENDPOINT`` set (the
    autouse fixture clears them), ``detect()`` excludes all six, so the first-detected
    provider — and thus ``resolve()`` — is exactly what it was before E12-S6.
    """
    detected_ids = {p.id for p in get_registry().detect()}
    assert detected_ids.isdisjoint({s.id for s in SPECS})


def test_frameworks_excluded_from_detection_in_isolation() -> None:
    """A throwaway registry of only the six frameworks detects nothing by default."""
    registry = ProviderRegistry()
    for spec in SPECS:
        registry.register(spec.provider_cls)
    assert registry.detect() == []


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_opt_in_endpoint_flips_detection(spec: Spec, monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting the endpoint env var makes exactly that framework auto-detect (opt-in on)."""
    registry = ProviderRegistry()
    registry.register(spec.provider_cls)
    assert registry.detect() == []

    monkeypatch.setenv(spec.endpoint_env, "http://opted.in/trace")
    detected = registry.detect()
    assert [p.id for p in detected] == [spec.id]
    assert detected[0].endpoint == "http://opted.in/trace"


def test_default_resolution_unchanged_when_frameworks_absent() -> None:
    """With only the reference + frameworks registered and no opt-in, ``resolve`` → copilot.

    Guards the #71 regression class from a different angle: an ingest-only framework that
    sorts before ``copilot`` (e.g. ``crewai``) must not win resolution by default.
    """
    from memrelay.providers.copilot import CopilotProvider

    registry = ProviderRegistry()
    registry.register(CopilotProvider)
    for spec in SPECS:
        registry.register(spec.provider_cls)

    resolved = registry.resolve(home="/nonexistent-home-for-test")
    assert resolved.id == DEFAULT_PROVIDER_ID
