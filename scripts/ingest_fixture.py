"""Dev entry point for the E0 walking skeleton (SPEC §3.2–§3.4).

Replays a Copilot ``events.jsonl`` — the committed redacted fixture by default —
through the real ``copilot.yaml`` adapter and a lean traceforge ``EventPipeline``
into a console sink, printing every normalized ``SessionEvent`` plus a tally.
No Graphiti, no network.

Usage::

    python scripts/ingest_fixture.py
    python scripts/ingest_fixture.py --events path/to/events.jsonl --session-id my-session
    python scripts/ingest_fixture.py --quiet        # summary only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from memrelay.ingest.fixture_runner import replay

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "copilot_session.jsonl"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a Copilot session into SessionEvents.")
    parser.add_argument("--events", default=str(DEFAULT_FIXTURE), help="path to events.jsonl")
    parser.add_argument("--session-id", default="fixture-session")
    parser.add_argument("--quiet", action="store_true", help="print the summary only")
    args = parser.parse_args()

    events_path = Path(args.events)
    if not events_path.is_file():
        raise SystemExit(f"events file not found: {events_path}")

    result = replay(events_path, args.session_id, echo=not args.quiet)
    print("\n" + "=" * 60)
    print(f"session:   {result.session_id}")
    print(f"parsed:    {result.parsed} SessionEvent(s) from adapter")
    print(f"delivered: {result.delivered} to sink (after pipeline enrich/filter)")
    print(f"elapsed:   {result.elapsed_s * 1000:.1f} ms")
    print("by kind:")
    for kind, count in result.by_kind.most_common():
        print(f"  {kind:<26} {count}")
    print("by visibility:")
    for vis, count in result.by_visibility.most_common():
        print(f"  {vis:<10} {count}")

    if result.delivered == 0:
        print("\nNO EVENTS DELIVERED — walking skeleton failed", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
