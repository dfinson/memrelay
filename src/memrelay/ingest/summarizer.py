"""Deterministic, offline episode compaction for the spool's disk budget (E3-S4 #33).

When the durable spool crosses its configured disk budget under backpressure (the
ingester is behind while the producer keeps appending), the ingester summarizes the
**oldest unprocessed** episodes *in place* to bound disk (see
:mod:`memrelay.ingest.ingester` and :meth:`memrelay.ingest.spool.Spool.replace`). This
module supplies the summarization seam.

Like :mod:`memrelay.ingest.backoff`, it is deliberately **pure and offline** — stdlib
only, no asyncio, no engine, no spool, no network — so the default is trivially
unit-testable and, crucially, so unit tests and the no-keys CI **never hit a real LLM or
need an API key**. Summarization "implies an LLM call" only for a *richer* future
summarizer; that would plug into the same :data:`Summarizer` seam (still inside
``ingest/**``, never on the frozen engine surface) without touching this default.

The default (:func:`default_summarizer`) groups the oldest records by ``namespace`` — the
graph scope must be preserved, so episodes from different namespaces are never merged —
and emits **one bounded summary record per namespace**. Each summary's ``content`` is a
capped digest (``≤`` :data:`MAX_SUMMARY_CHARS`), which is what makes compaction reclaim
disk: many large episodes collapse into a few small ones. The summary's
``idempotency_key`` is a deterministic hash of its members' keys, so re-running the same
compaction (e.g. after a crash rolled back the replace) yields the identical key.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from memrelay.ingest.episode import EpisodeRecord

#: Upper bound (characters) on a single compaction summary's ``content``. The digest is
#: truncated to this, so a summary's size is independent of how large the compacted
#: episodes were — the guarantee that lets a budget-triggered compaction bound disk.
MAX_SUMMARY_CHARS = 512

#: Per-episode excerpt cap folded into the digest, so one huge episode cannot crowd out
#: every other episode's contribution before the overall :data:`MAX_SUMMARY_CHARS` clamp.
_PER_EPISODE_CHARS = 120

#: Marker prefixing a summary's content so a compacted episode is recognizable in recall.
_SUMMARY_PREFIX = "[memrelay compaction]"

#: A summarizer: fold a batch of the oldest episode dicts into fewer, size-bounded ones.
#: The default is :func:`default_summarizer`; the ingester takes it as an injectable seam.
Summarizer = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def _summary_key(member_keys: list[str]) -> str:
    """Return a deterministic, collision-resistant key for a summary of ``member_keys``.

    Order-independent (the *set* of compacted keys determines the summary), so a
    re-attempted compaction of the same rows produces the identical key.
    """
    hasher = hashlib.sha256()
    hasher.update(b"memrelay-compaction-summary\x00")
    for key in sorted(member_keys):
        hasher.update(key.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _digest(contents: list[str]) -> str:
    """Fold episode contents into one whitespace-normalized, length-bounded string."""
    parts: list[str] = []
    for content in contents:
        collapsed = " ".join(str(content).split())
        if collapsed:
            parts.append(collapsed[:_PER_EPISODE_CHARS])
    joined = " | ".join(parts)
    if len(joined) > MAX_SUMMARY_CHARS:
        joined = joined[: MAX_SUMMARY_CHARS - 3].rstrip() + "..."
    return joined


def default_summarizer(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact ``records`` into one bounded summary episode per ``namespace``.

    Deterministic and offline: no LLM, no network, no keys. Records are grouped by
    ``namespace`` (preserving graph scope); within a group ``repo`` is carried through
    only if uniform (else ``None``), ``ts`` is the latest member's, ``source`` is marked
    ``"compaction"``, ``phase`` is dropped, and ``idempotency_key`` is a deterministic
    hash of the members' keys. The returned list is ordered by namespace for stable
    output. An empty input yields an empty list.
    """
    groups: dict[Any, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(record.get("namespace"), []).append(record)

    summaries: list[dict[str, Any]] = []
    for namespace in sorted(groups, key=lambda ns: "" if ns is None else str(ns)):
        members = groups[namespace]
        repos = {member.get("repo") for member in members}
        repo = next(iter(repos)) if len(repos) == 1 else None
        timestamps = [str(member.get("ts") or "") for member in members]
        latest_ts = max(timestamps) if timestamps else ""
        member_keys = [str(member.get("idempotency_key") or "") for member in members]
        digest = _digest([str(member.get("content", "")) for member in members])
        content = f"{_SUMMARY_PREFIX} {len(members)} episode(s): {digest}"
        summary = EpisodeRecord.new(
            content,
            namespace if isinstance(namespace, str) else "",
            repo=repo,
            source="compaction",
            ts=latest_ts,
            idempotency_key=_summary_key(member_keys),
        )
        summaries.append(summary.to_dict())
    return summaries
