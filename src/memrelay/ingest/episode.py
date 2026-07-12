"""The episode record: the unit that crosses the durable spool (E2-S1 #24, E2-S5 #28).

An *episode* is one normalized fact on its way from a producer (the observation
side) into the Graphiti memory engine. It is deliberately a **plain,
JSON-serializable dict** in transit so it can be written to the SQLite spool and
read back in a different process without any custom (de)serialization:

    record = EpisodeRecord.new("a fact", namespace="proj-a", repo="memrelay").to_dict()
    spool.append(record)                    # producer side
    for seq, record in spool.read_batch():  # ingester side
        await engine.note(record["content"], record["namespace"], record.get("repo"))

:class:`EpisodeRecord` is the typed constructor/validator for that dict;
:func:`make_idempotency_key` derives the stable key the spool dedups on; and
:func:`to_row` / :func:`from_row` are the single serialization seam the spool uses
for its ``record`` column (kept here so the wire form is defined in exactly one
place). This module is intentionally pure-stdlib — no traceforge, no graphiti — so
both the producer and the ingester can depend on it cheaply.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

#: The exact keys of the serialized episode dict, in declaration order. A & C build
#: dicts against this; :func:`EpisodeRecord.from_dict` filters to it defensively.
#: ``phase`` (E2-S6 #98), then ``last_commit_sha`` / ``file_change_lines`` (E9-S3 #60)
#: are appended last and default to ``None`` so that spool rows written before they
#: existed still deserialize (the missing key falls back to the field default) — a
#: backward-compatible wire-format extension.
EPISODE_FIELDS: tuple[str, ...] = (
    "content",
    "namespace",
    "repo",
    "source",
    "session_id",
    "event_id",
    "ts",
    "idempotency_key",
    "phase",
    "last_commit_sha",
    "file_change_lines",
)


def make_idempotency_key(session_id: str | None, event_id: str | None, content: str) -> str:
    """Return a stable hex digest identifying one episode for dedup.

    The spool's ``INSERT OR IGNORE`` dedups on this key, so it must be
    deterministic for a given ``(session_id, event_id, content)`` and differ when
    any of them differ. A NUL separator keeps the parts unambiguous (so
    ``("a", "b")`` and ``("ab", "")`` never collide). ``None`` is treated as the
    empty string, which lets a producer key purely off ``content`` when it has no
    session/event identity yet.
    """
    hasher = hashlib.sha256()
    for part in (session_id, event_id, content):
        hasher.update((part or "").encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    """A single fact bound for the memory engine.

    Only ``content`` / ``namespace`` / ``repo`` are consumed by the ingester (they
    map straight onto ``engine.note``); the rest is provenance the spool carries
    through unchanged. ``phase`` (E2-S6 #98) is the derived traceforge
    workflow-phase for the episode when ``enable_phase`` is on (else ``None``); the
    ingester folds it into the noted content, so it is *not* an input to
    :func:`make_idempotency_key` and never changes an episode's key. ``last_commit_sha``
    / ``file_change_lines`` (E9-S3 #60) are file-refactor provenance stamped by the sink
    only when ``refactor_invalidation_lines`` is enabled: the HEAD sha the file episode
    was observed at, and a ``{path: changed_lines}`` magnitude map. Both default ``None``
    (so the zero-config wire form is unchanged), are pure provenance the ingester forwards
    to ``note`` unchanged, and — like ``phase`` — are *not* part of the idempotency key.
    Instances are frozen — treat a record as an immutable value and use :meth:`to_dict`
    to get the transport form.
    """

    content: str
    namespace: str
    repo: str | None = None
    source: str = "unknown"
    session_id: str | None = None
    event_id: str | None = None
    ts: str = ""
    idempotency_key: str = ""
    phase: str | None = None
    last_commit_sha: str | None = None
    file_change_lines: dict[str, int] | None = None

    @classmethod
    def new(
        cls,
        content: str,
        namespace: str,
        *,
        repo: str | None = None,
        source: str = "unknown",
        session_id: str | None = None,
        event_id: str | None = None,
        ts: str | None = None,
        idempotency_key: str | None = None,
        phase: str | None = None,
        last_commit_sha: str | None = None,
        file_change_lines: dict[str, int] | None = None,
    ) -> EpisodeRecord:
        """Build a record, filling ``ts`` (now, UTC ISO-8601) and the idempotency key.

        Pass ``ts`` / ``idempotency_key`` explicitly to override the defaults; leave
        them ``None`` and the record stamps the current time and derives the key
        from ``(session_id, event_id, content)`` via :func:`make_idempotency_key`.
        ``phase`` is optional derived provenance (E2-S6 #98) and, by design, is *not*
        part of the idempotency key — enabling phase never changes an episode's key.
        ``last_commit_sha`` / ``file_change_lines`` are optional file-refactor provenance
        (E9-S3 #60), likewise excluded from the idempotency key.
        """
        resolved_ts = ts if ts is not None else datetime.now(UTC).isoformat()
        resolved_key = (
            idempotency_key
            if idempotency_key is not None
            else make_idempotency_key(session_id, event_id, content)
        )
        return cls(
            content=content,
            namespace=namespace,
            repo=repo,
            source=source,
            session_id=session_id,
            event_id=event_id,
            ts=resolved_ts,
            idempotency_key=resolved_key,
            phase=phase,
            last_commit_sha=last_commit_sha,
            file_change_lines=file_change_lines,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the plain, JSON-serializable dict form (the spool's currency)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodeRecord:
        """Rebuild a record from a dict, ignoring any unknown keys defensively."""
        known = {key: data[key] for key in EPISODE_FIELDS if key in data}
        return cls(**known)


def to_row(record: dict[str, Any]) -> str:
    """Serialize an episode dict to the canonical JSON text stored by the spool.

    ``sort_keys`` makes the stored text stable regardless of the producer's dict
    ordering; :func:`from_row` is its exact inverse.
    """
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def from_row(row: str) -> dict[str, Any]:
    """Parse the spool's ``record`` column text back into an episode dict."""
    return json.loads(row)
