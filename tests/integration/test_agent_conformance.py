"""Multi-agent golden-replay conformance matrix (E11-S8, #66).

A single, **registry-driven** conformance surface so agent format drift is caught in CI:

* :func:`test_every_registered_provider_has_a_conformance_fixture` — the *"adding a provider
  REQUIRES a fixture"* guard. It is parametrized over the **live** provider registry
  (``get_registry().ids()``), so the instant a provider self-registers with ``@register`` it must
  ship a matching ``tests/fixtures/<id>_session.jsonl`` or this test turns CI red.

* :func:`test_agent_conformance_matrix` — the per-agent golden replay. For every registered
  provider it drives that agent's recorded fixture through the provider's **TraceForge replay
  source** (:meth:`~memrelay.providers.base.AgentProvider.make_source`) + **adapter**
  (:meth:`~memrelay.providers.base.AgentProvider.make_adapter`) and asserts every produced
  ``SessionEvent`` conforms in **shape**, **visibility**, and **phase**.

Because both tests parametrize over the registry, a *new* provider automatically joins the matrix
— the whole point of the story. The replay is **hermetic and upstream of the engine**: an explicit
fixture ``path=`` means no real agent home is read, and the assertions are on parsed
``SessionEvent``s only (no Graphiti, no network, no keys, no LLM/embedder). The existing per-agent
tests are intentionally *not* a substitute: ``test_walking_skeleton.py`` is copilot-only and reads
its fixture with ``open()`` (not the replay source), and ``test_claude_provider.py`` asserts only a
subset of kinds with no visibility/phase/shape checks.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from traceforge import EventKind, EventMetadata, Phase, SessionEvent, Visibility

from memrelay.providers.registry import get_registry

#: ``tests/fixtures`` — this file lives at ``tests/integration/``, so ``parents[1]`` is ``tests``.
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _fixture_path(agent_id: str) -> Path:
    """The recorded conformance fixture for ``agent_id`` (``<id>_session.jsonl`` convention)."""
    return FIXTURES_DIR / f"{agent_id}_session.jsonl"


#: The provider ids memrelay ships, taken straight from the live registry. Parametrizing over this
#: is what makes a newly-registered provider automatically require a fixture and join the matrix.
REGISTERED_IDS = get_registry().ids()


def _canonical_kinds() -> frozenset[str]:
    """The set of canonical dotted event kinds traceforge's ``EventKind`` defines."""
    kinds: set[str] = set()
    for name in dir(EventKind):
        if name.startswith("__"):
            continue
        value = getattr(EventKind, name)
        if isinstance(value, str):
            kinds.add(value)
    return frozenset(kinds)


#: Canonical kinds a parsed ``SessionEvent`` may carry. A kind outside this set is agent format
#: drift (a mapping emitting something traceforge does not recognize).
CANONICAL_KINDS = _canonical_kinds()

#: SPEC §3.4 visibility band. ``Visibility`` is a 3-member enum today; asserting the *value* against
#: this literal set means a future traceforge visibility addition becomes a deliberate CI review
#: rather than silently passing.
VALID_VISIBILITIES = frozenset({"visible", "system", "collapsed"})

#: A recorded fixture must be a real mini-session, not a stub: floor the replayed event count so a
#: truncated or empty fixture fails loudly instead of vacuously passing.
MIN_EVENTS = 3


# ── criterion 4: adding a provider REQUIRES a conformance fixture ─────────────


def test_registry_is_non_empty() -> None:
    """A matrix parametrized over an empty registry would pass vacuously — guard against it."""
    assert REGISTERED_IDS, "provider registry is empty; expected at least the reference provider"


@pytest.mark.parametrize("agent_id", REGISTERED_IDS)
def test_every_registered_provider_has_a_conformance_fixture(agent_id: str) -> None:
    """Every registered provider must ship a recorded golden-replay fixture (#66 criterion 4).

    A provider joins the registry by decorating its class with ``@register``; the moment it does,
    this parametrized case demands a matching ``tests/fixtures/<id>_session.jsonl``. So a provider
    merged without a fixture turns CI red here (and in :func:`test_agent_conformance_matrix`) until
    a fixture is captured for it.
    """
    fixture = _fixture_path(agent_id)
    assert fixture.is_file(), (
        f"provider {agent_id!r} is registered but its conformance fixture is missing: {fixture}. "
        f"Capture one (see tests/fixtures/README.md) — every supported agent needs a recorded "
        f"golden-replay fixture."
    )


# ── criteria 1-2: golden-replay shape + visibility + phase, per agent ────────


def _replay(agent_id: str, session_id: str) -> list[SessionEvent]:
    """Replay ``<id>_session.jsonl`` through the provider's replay source + adapter.

    A registry-built provider plus an explicit fixture ``path=`` means the agent's real home is
    never touched (fully hermetic). ``make_source(path=...)`` is the TraceForge replay source (it
    yields raw JSONL lines); ``make_adapter(session_id).parse(line)`` normalizes each line to
    ``SessionEvent``s scoped to ``session_id``.
    """
    provider = get_registry().create(agent_id)
    adapter = provider.make_adapter(session_id)
    events: list[SessionEvent] = []
    for line in provider.make_source(path=_fixture_path(agent_id)):
        events.extend(adapter.parse(line))
    return events


@pytest.mark.parametrize("agent_id", REGISTERED_IDS)
def test_agent_conformance_matrix(agent_id: str) -> None:
    """Golden-replay conformance for one supported agent (#66 criteria 1-2).

    Drives the agent's recorded fixture through its TraceForge replay source + adapter and asserts
    every produced ``SessionEvent`` conforms in shape, visibility, and phase. Parametrized over the
    live registry, so this is the per-agent matrix that catches format drift in CI.
    """
    session_id = f"conformance-{agent_id}"
    events = _replay(agent_id, session_id)

    assert len(events) >= MIN_EVENTS, (
        f"{agent_id}: fixture replayed to only {len(events)} event(s); expected >= {MIN_EVENTS} — "
        f"is {_fixture_path(agent_id).name} truncated?"
    )

    for event in events:
        # ── shape ────────────────────────────────────────────────────────────
        assert isinstance(event, SessionEvent)
        assert event.session_id == session_id, (
            f"{agent_id}: adapter must stamp the scoped session_id, got {event.session_id!r}"
        )
        kind = str(event.kind)
        assert kind, f"{agent_id}: event carries an empty kind"
        assert kind in CANONICAL_KINDS, (
            f"{agent_id}: unrecognized event kind {kind!r} — agent format drift "
            f"(not a canonical traceforge EventKind)"
        )
        assert isinstance(event.timestamp, datetime), (
            f"{agent_id}: event.timestamp must be a datetime, got {type(event.timestamp)!r}"
        )
        assert isinstance(event.metadata, EventMetadata)

        # ── visibility (SPEC §3.4) ───────────────────────────────────────────
        visibility = event.metadata.visibility
        assert isinstance(visibility, Visibility), (
            f"{agent_id}: metadata.visibility must be a Visibility enum, got {visibility!r}"
        )
        assert visibility.value in VALID_VISIBILITIES, (
            f"{agent_id}: unexpected visibility {visibility.value!r} for kind {kind!r}"
        )

        # ── phase ────────────────────────────────────────────────────────────
        # Phase is a pipeline-stamped, opt-in ML field (config.ingest.enable_phase). At this
        # adapter/replay layer — upstream of the engine — it is unset, so the conformance contract
        # is that the field is *well-formed*: None, or a valid Phase. End-to-end phase stamping is
        # covered by tests/integration/test_phase_enrichment_e2e.py.
        phase = event.metadata.phase
        assert phase is None or isinstance(phase, Phase), (
            f"{agent_id}: metadata.phase must be None or a Phase, got {phase!r}"
        )
        phases = event.metadata.phases
        assert phases is None or (
            isinstance(phases, frozenset) and all(isinstance(p, Phase) for p in phases)
        ), f"{agent_id}: metadata.phases must be None or a frozenset[Phase], got {phases!r}"
