"""E0-S5 walking skeleton: one real (redacted) Copilot session, end-to-end.

Proves the de-risked path from SPEC §3: a real Copilot ``events.jsonl`` maps
through traceforge's ``copilot.yaml`` adapter into ``SessionEvent``s and flows
through a lean ``EventPipeline`` to a sink — with **no Graphiti** and no network.

The strict assertions are on the deterministic *adapter* output; the *pipeline*
assertions are looser because enrichment/coalescing behavior belongs to
traceforge and may evolve.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from memrelay.ingest.fixture_runner import replay
from memrelay.providers.copilot import CopilotProvider

#: The exact kind histogram the copilot.yaml adapter produces for the fixture
#: (verified by scripts/capture_fixture.py — redaction preserves it). The fixture
#: is a minimal 14-record excerpt: exactly one event per mapped kind, including
#: file.edited (from a raw ``session.workspace_file_changed`` record).
EXPECTED_ADAPTER_KINDS: dict[str, int] = {
    "session.started": 1,
    "message.system": 1,
    "message.user": 1,
    "turn.started": 1,
    "message.assistant": 1,
    "tool.call.started": 1,
    "tool.call.completed": 1,
    "permission.requested": 1,
    "permission.granted": 1,
    "hook.started": 1,
    "hook.completed": 1,
    "file.edited": 1,
    "turn.ended": 1,
    "session.ended": 1,
}
EXPECTED_TOTAL = sum(EXPECTED_ADAPTER_KINDS.values())  # 14

VALID_VISIBILITIES = {"visible", "system", "collapsed"}


def _adapter_kind_histogram(fixture: Path) -> Counter[str]:
    adapter = CopilotProvider().make_adapter("fixture-session")
    hist: Counter[str] = Counter()
    with open(fixture, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            for event in adapter.parse(line):
                hist[str(event.kind)] += 1
    return hist


def test_adapter_maps_fixture_to_expected_kinds(copilot_fixture: Path) -> None:
    """The copilot mapping deterministically yields the expected SessionEvents."""
    hist = _adapter_kind_histogram(copilot_fixture)
    assert dict(hist) == EXPECTED_ADAPTER_KINDS
    assert sum(hist.values()) == EXPECTED_TOTAL


def test_adapter_events_carry_valid_visibility(copilot_fixture: Path) -> None:
    """Every SessionEvent exposes visibility at ``metadata.visibility`` (SPEC §3.4)."""
    adapter = CopilotProvider().make_adapter("fixture-session")
    seen = set()
    with open(copilot_fixture, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            for event in adapter.parse(line):
                assert event.session_id == "fixture-session"
                seen.add(str(event.metadata.visibility))
    assert seen, "no events produced"
    assert seen <= VALID_VISIBILITIES, f"unexpected visibility values: {seen}"


def test_walking_skeleton_pipeline_delivers(copilot_fixture: Path) -> None:
    """Fixture flows through EventPipeline to the console sink end-to-end."""
    collected: list[str] = []
    result = replay(copilot_fixture, "fixture-session", echo=True, writer=collected.append)

    # Adapter output is exact; pipeline delivery is a subset (enricher coalesces
    # tool.call.started into its completed pair, so delivered == parsed - 1).
    assert result.parsed == EXPECTED_TOTAL
    assert 0 < result.delivered <= result.parsed
    assert len(collected) == result.delivered

    # Stable, mapping-driven kinds must survive enrichment.
    assert result.by_kind["session.started"] == 1
    assert result.by_kind["session.ended"] == 1
    assert result.by_kind["message.user"] == 1
    assert result.by_kind["tool.call.completed"] == 1
    assert result.by_kind["file.edited"] == 1

    assert set(result.by_visibility) <= VALID_VISIBILITIES
    assert result.by_visibility["visible"] > 0


def test_runner_forwards_ingest_flags(copilot_fixture: Path, monkeypatch) -> None:
    """replay() forwards IngestConfig.enable_phase/boundary into EventPipeline (Point 5).

    Later epics flip these on via config; E0 must prove the wiring reaches the
    pipeline constructor rather than being hardcoded.
    """
    import traceforge

    from memrelay.config import Config, IngestConfig

    captured: dict[str, bool] = {}

    class SpyPipeline:
        def __init__(self, *, sinks, enricher, governance, enable_phase, enable_boundary):
            captured["enable_phase"] = enable_phase
            captured["enable_boundary"] = enable_boundary

        async def push(self, event) -> None:  # noqa: ANN001 - test spy
            pass

        async def flush(self) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr(traceforge, "EventPipeline", SpyPipeline)

    cfg = Config(ingest=IngestConfig(enable_phase=True, enable_boundary=True))
    replay(copilot_fixture, "fixture-session", echo=False, config=cfg)
    assert captured == {"enable_phase": True, "enable_boundary": True}


def test_adapter_never_raises_on_bad_input() -> None:
    """``adapter.parse`` is contracted never to raise, whatever the input (SPEC §3.2)."""
    adapter = CopilotProvider().make_adapter("fixture-session")
    for junk in ("", "   ", "not json", "{unbalanced", '{"type": 123}', "[]", "null", "42"):
        events = list(adapter.parse(junk))  # must not raise
        for event in events:
            assert event.session_id == "fixture-session"


def test_adapter_drops_malformed_json() -> None:
    """Unparseable lines are dropped defensively (logged, not raised)."""
    adapter = CopilotProvider().make_adapter("fixture-session")
    for bad in ("", "not json", "{unbalanced", "null"):
        assert list(adapter.parse(bad)) == []


def test_adapter_maps_unknown_type_to_raw() -> None:
    """A structurally valid but unmapped record falls through to ``default_kind: raw``.

    This is *not* a drop: only malformed JSON is discarded. Unknown event types are
    preserved as ``raw`` (see docs/e0-spike.md — the copilot.yaml default_kind).
    """
    adapter = CopilotProvider().make_adapter("fixture-session")
    line = (
        '{"type": "totally.unknown.event", "id": "z",'
        ' "timestamp": "2026-01-01T00:00:00Z", "data": {}}'
    )
    events = list(adapter.parse(line))
    assert len(events) == 1
    assert str(events[0].kind) == "raw"
