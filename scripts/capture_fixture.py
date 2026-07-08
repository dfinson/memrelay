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

After writing, it replays both the original and the redacted files through the
real adapter and asserts the produced ``SessionEvent`` **kind histogram is
identical** — proving redaction did not change how the trace maps.

Usage::

    python scripts/capture_fixture.py                 # auto-pick a session
    python scripts/capture_fixture.py --session-id <id>
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


# ── Main ─────────────────────────────────────────────────────────────────────


def capture(copilot_home: Path, session_id: str, out_path: Path) -> None:
    src = _session_root(copilot_home) / session_id / "events.jsonl"
    if not src.is_file():
        raise SystemExit(f"no events.jsonl for session {session_id!r} at {src}")

    redactor = Redactor()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with (
        open(src, encoding="utf-8") as fh,
        open(out_path, "w", encoding="utf-8", newline="\n") as out,
    ):
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.write(json.dumps(redactor.redact(record), ensure_ascii=False) + "\n")
            kept += 1

    print(f"captured {kept} redacted records: {src}  ->  {out_path}")

    # Self-verify: redaction must not change how the trace maps.
    original = kind_histogram(src, "fixture-session")
    redacted = kind_histogram(out_path, "fixture-session")
    if original == redacted:
        print(f"OK  kind histogram identical after redaction ({sum(redacted.values())} events)")
    else:
        print("MISMATCH  redaction changed the kind histogram:")
        print("  only in original:", original - redacted)
        print("  only in redacted:", redacted - original)
        raise SystemExit(1)
    for kind, count in redacted.most_common():
        print(f"  {kind:<26} {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a redacted Copilot fixture.")
    parser.add_argument("--copilot-home", default=str(Path("~/.copilot").expanduser()))
    parser.add_argument("--session-id", default=None, help="default: auto-pick")
    default_out = (
        Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "copilot_session.jsonl"
    )
    parser.add_argument("--out", default=str(default_out))
    args = parser.parse_args()

    home = Path(args.copilot_home).expanduser()
    session_id = args.session_id or auto_pick(home)
    if not session_id:
        raise SystemExit("no suitable session found; pass --session-id explicitly")
    print(f"session: {session_id}")
    capture(home, session_id, Path(args.out))


if __name__ == "__main__":
    main()
