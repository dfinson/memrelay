"""A console ``StorageSink`` for the E0 walking skeleton.

Subclasses traceforge's ``StorageSink`` (rather than using a bare callback) so it
mirrors the shape the real ``GraphitiSink`` will take — async ``on_event`` plus a
``flush``/``close`` lifecycle — and demonstrates the SPEC §3.4 visibility filter
without importing Graphiti.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING

from traceforge import StorageSink

if TYPE_CHECKING:
    from traceforge import SessionEvent

Writer = Callable[[str], None]


class ConsoleSink(StorageSink):
    """Print each event and tally counts by kind and visibility.

    Args:
        writer: line sink (defaults to ``print``); injectable for tests.
        echo: when ``True``, print a compact line per event as it arrives.
    """

    def __init__(self, writer: Writer = print, *, echo: bool = True) -> None:
        self._write = writer
        self._echo = echo
        self.total = 0
        self.by_kind: Counter[str] = Counter()
        self.by_visibility: Counter[str] = Counter()

    async def on_event(self, event: SessionEvent) -> None:
        self.total += 1
        kind = str(getattr(event, "kind", "?"))
        visibility = str(getattr(event.metadata, "visibility", "?"))
        self.by_kind[kind] += 1
        self.by_visibility[visibility] += 1
        if self._echo:
            ts = getattr(event, "timestamp", "")
            self._write(f"[{visibility:<9}] {kind:<24} {ts}")

    async def flush(self) -> None:  # noqa: D102 - lifecycle no-op for console output
        return None

    async def close(self) -> None:  # noqa: D102 - lifecycle no-op for console output
        return None

    def summary(self) -> str:
        """A human-readable multi-line tally of what was ingested."""
        lines = [f"total events: {self.total}", "by kind:"]
        lines += [f"  {kind:<24} {count}" for kind, count in self.by_kind.most_common()]
        lines.append("by visibility:")
        lines += [f"  {vis:<10} {count}" for vis, count in self.by_visibility.most_common()]
        return "\n".join(lines)
