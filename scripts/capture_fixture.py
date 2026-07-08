"""Capture a **redacted** Copilot ``events.jsonl`` as a test fixture (E0-S1).

Reads a real ``~/.copilot/session-state/<id>/events.jsonl``, scrubs every
content-bearing string (messages, reasoning, file paths, cwd, tool arguments,
tool output, summaries) while preserving the exact wire structure the
``copilot.yaml`` mapping depends on (``type`` discriminator, ``timestamp``, enum
fields, numbers, booleans, and id linkage), and writes the result to
``tests/fixtures/``.

The scrub is *structure-preserving*: it keeps keys and value types so the fixture
remains a faithful mapping regression sample, but replaces free text with
``[redacted]`` and remaps every id to a deterministic placeholder (so
``parentId`` links survive de-identification).

After writing, it replays the selected records and the redacted output through
the real adapter and asserts the produced ``SessionEvent`` **kind histogram is
identical** — proving redaction did not change how the trace maps.

By default the capture is also **minimized** (``--minimal``, on by default): from
the sampled session it keeps one *coherent* representative of each event category
the walking-skeleton test needs — messages, a turn, a tool call, a permission
exchange, a hook, and a file edit — rather than the full, redundant trace. Paired
events (tool/turn/hook) keep a matching id so the pipeline's coalescing stays
meaningful, and a synthetic ``file.edited`` record is injected when the sampled
session never touched the workspace. Pass ``--full`` to redact the whole session.

Usage::

    python scripts/capture_fixture.py                 # auto-pick, minimal fixture
    python scripts/capture_fixture.py --session-id <id>
    python scripts/capture_fixture.py --full          # whole session, redacted
    python scripts/capture_fixture.py --out tests/fixtures/copilot_session.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

# ── Redaction policy ─────────────────────────────────────────────────────────

#: String values under these keys are kept verbatim — they are enums, model
#: names, tool names, or timestamps, none of which are user content.
SAFE_STRING_KEYS = frozenset(
    {
        "type",
        "kind",
        "timestamp",
        "selectedModel",
        "copilotVersion",
        "model",
        "newModel",
        "reasoningEffort",
        "contextTier",
        "shutdownType",
        "operation",
        "hookType",
        "toolName",
        "skillName",
        "mode",
        "role",
        "status",
        "level",
        "phase",
    }
)

#: Values under these keys are ids: remapped deterministically to preserve links
#: (parentId → id) while de-identifying.
ID_KEYS = frozenset(
    {
        "id",
        "parentId",
        "sessionId",
        "toolCallId",
        "turnId",
        "hookInvocationId",
        "hookId",
        "agentId",
        "requestId",
        "messageId",
    }
)

PLACEHOLDER = "[redacted]"


class Redactor:
    """Structure-preserving redactor with stable id remapping."""

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}

    def _remap_id(self, value: object) -> object:
        if not isinstance(value, str):
            return value
        if value not in self._ids:
            self._ids[value] = f"00000000-0000-4000-8000-{len(self._ids):012d}"
        return self._ids[value]

    def redact(self, obj: object, key: str | None = None) -> object:
        if isinstance(obj, dict):
            return {k: self.redact(v, k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.redact(v, key) for v in obj]
        if key in ID_KEYS:
            return self._remap_id(obj)
        if isinstance(obj, str):
            if key in SAFE_STRING_KEYS:
                return obj
            return PLACEHOLDER if obj else obj
        return obj  # numbers, booleans, null pass through


# ── Minimal coherent selection ───────────────────────────────────────────────

#: A real-*shaped* ``session.workspace_file_changed`` record — the raw wire type
#: the copilot.yaml mapping turns into ``file.edited`` (payload: path, operation).
#: The shape was confirmed against live data; the content here is **synthetic**
#: (no real path or id is embedded) so the fixture always exercises file.edited
#: even when the sampled session never touched the workspace. operation ∈
#: {create, edit, delete}.
FILE_CHANGED_EXEMPLAR: dict = {
    "type": "session.workspace_file_changed",
    "data": {"path": "src/example/module.py", "operation": "edit"},
    "id": "file-changed-exemplar",
    "timestamp": "2026-01-01T00:00:00.000Z",
    "parentId": "turn-exemplar-parent",
}

#: (start type, end type, shared ``data.*`` id) — the minimal fixture keeps a
#: *coherent* pair (same tool call / turn / hook), not two unrelated events.
_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("tool.execution_start", "tool.execution_complete", "toolCallId"),
    ("assistant.turn_start", "assistant.turn_end", "turnId"),
    ("hook.start", "hook.end", "hookInvocationId"),
)

#: Wire types kept as a single first-seen record (no pairing id needed).
_SINGLETONS: tuple[str, ...] = (
    "session.start",
    "user.message",
    "assistant.message",
    "system.message",
    "permission.requested",
    "permission.completed",
    "session.shutdown",
)

#: Emit order for the minimal fixture — a coherent one-turn mini-session,
#: ordered to match real event chronology.
_MINIMAL_ORDER: tuple[str, ...] = (
    "session.start",
    "system.message",
    "user.message",
    "assistant.turn_start",
    "assistant.message",
    "tool.execution_start",
    "permission.requested",
    "permission.completed",
    "hook.start",
    "hook.end",
    "tool.execution_complete",
    "session.workspace_file_changed",
    "assistant.turn_end",
    "session.shutdown",
)


def select_minimal(records: list[dict]) -> list[dict]:
    """Reduce a full session to the minimum coherent set of records that still
    exercises every category the walking-skeleton test needs: messages, a turn,
    a tool call, a permission exchange, a hook, and a file edit.

    Paired events keep a matching id so the pipeline's tool/turn/hook coalescing
    is meaningful; a synthetic ``file.edited`` record is injected when the sampled
    session never touched the workspace.
    """
    by_type: dict[str, list[dict]] = {}
    for record in records:
        by_type.setdefault(str(record.get("type", "")), []).append(record)

    chosen: dict[str, dict] = {}
    for start_t, end_t, link in _PAIRS:
        starts, ends = by_type.get(start_t, []), by_type.get(end_t, [])
        if starts and ends:
            start = starts[0]
            start_id = start.get("data", {}).get(link)
            end = next((e for e in ends if e.get("data", {}).get(link) == start_id), ends[0])
            chosen[start_t] = start
            chosen[end_t] = end
    for wire_type in _SINGLETONS:
        if by_type.get(wire_type):
            chosen[wire_type] = by_type[wire_type][0]
    chosen.setdefault("session.workspace_file_changed", FILE_CHANGED_EXEMPLAR)

    ordered = [chosen[t] for t in _MINIMAL_ORDER if t in chosen]
    ordered += [r for t, r in chosen.items() if t not in _MINIMAL_ORDER]
    return ordered


# ── Session selection ────────────────────────────────────────────────────────


def _session_root(copilot_home: Path) -> Path:
    return copilot_home / "session-state"


def _iter_sessions(copilot_home: Path):
    root = _session_root(copilot_home)
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        events = child / "events.jsonl"
        if events.is_file():
            yield child.name, events


def _score(events_path: Path) -> tuple[int, int]:
    """Return (distinct wire types, line count) for auto-pick scoring."""
    types: set[str] = set()
    lines = 0
    with open(events_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            lines += 1
            try:
                types.add(json.loads(line).get("type", ""))
            except json.JSONDecodeError:
                continue
    return len(types), lines


def auto_pick(copilot_home: Path, lo: int = 60, hi: int = 260) -> str | None:
    """Pick the session with the most distinct event types in [lo, hi] lines."""
    best: tuple[int, int, str] | None = None
    for sid, events in _iter_sessions(copilot_home):
        distinct, lines = _score(events)
        if lines < lo or lines > hi:
            continue
        candidate = (distinct, -lines, sid)  # more types, then fewer lines
        if best is None or candidate > best:
            best = candidate
    return best[2] if best else None


# ── Fixture kinds (for self-verification) ────────────────────────────────────


def kind_histogram(events_path: Path, session_id: str) -> Counter[str]:
    """Replay a JSONL file through the real copilot adapter; count kinds."""
    from memrelay.providers.copilot import CopilotProvider

    adapter = CopilotProvider().make_adapter(session_id)
    hist: Counter[str] = Counter()
    with open(events_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            for event in adapter.parse(line):
                hist[str(event.kind)] += 1
    return hist


def hist_from_records(records: list[dict], session_id: str) -> Counter[str]:
    """Map in-memory records through the real copilot adapter; count kinds."""
    from memrelay.providers.copilot import CopilotProvider

    adapter = CopilotProvider().make_adapter(session_id)
    hist: Counter[str] = Counter()
    for record in records:
        for event in adapter.parse_dict(record):
            hist[str(event.kind)] += 1
    return hist


# ── Main ─────────────────────────────────────────────────────────────────────


def capture(copilot_home: Path, session_id: str, out_path: Path, *, minimal: bool = True) -> None:
    src = _session_root(copilot_home) / session_id / "events.jsonl"
    if not src.is_file():
        raise SystemExit(f"no events.jsonl for session {session_id!r} at {src}")

    records: list[dict] = []
    with open(src, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    selected = select_minimal(records) if minimal else records

    redactor = Redactor()
    redacted = [redactor.redact(record) for record in selected]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as out:
        for record in redacted:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    scope = "minimal" if minimal else "full"
    print(f"captured {len(redacted)} redacted records ({scope}): {src}  ->  {out_path}")

    # Self-verify: redaction must not change how the trace maps.
    original = hist_from_records(selected, "fixture-session")
    result = hist_from_records(redacted, "fixture-session")  # type: ignore[arg-type]
    if original == result:
        print(f"OK  kind histogram identical after redaction ({sum(result.values())} events)")
    else:
        print("MISMATCH  redaction changed the kind histogram:")
        print("  only in original:", original - result)
        print("  only in redacted:", result - original)
        raise SystemExit(1)
    for kind, count in sorted(result.items()):
        print(f"  {kind:<26} {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a redacted Copilot fixture.")
    parser.add_argument("--copilot-home", default=str(Path("~/.copilot").expanduser()))
    parser.add_argument("--session-id", default=None, help="default: auto-pick")
    default_out = (
        Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "copilot_session.jsonl"
    )
    parser.add_argument("--out", default=str(default_out))
    parser.add_argument(
        "--full",
        action="store_true",
        help="redact the whole session instead of the minimal coherent subset",
    )
    args = parser.parse_args()

    home = Path(args.copilot_home).expanduser()
    session_id = args.session_id or auto_pick(home)
    if not session_id:
        raise SystemExit("no suitable session found; pass --session-id explicitly")
    print(f"session: {session_id}")
    capture(home, session_id, Path(args.out), minimal=not args.full)


if __name__ == "__main__":
    main()
