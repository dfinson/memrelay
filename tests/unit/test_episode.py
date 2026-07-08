"""Unit tests for the episode record + idempotency key (E2-S1 #24, E2-S5 #28)."""

from __future__ import annotations

from memrelay.ingest.episode import (
    EPISODE_FIELDS,
    EpisodeRecord,
    from_row,
    make_idempotency_key,
    to_row,
)


def test_idempotency_key_is_deterministic() -> None:
    key1 = make_idempotency_key("s1", "e1", "hello")
    key2 = make_idempotency_key("s1", "e1", "hello")
    assert key1 == key2
    assert isinstance(key1, str) and len(key1) == 64  # sha256 hex


def test_idempotency_key_varies_with_each_part() -> None:
    base = make_idempotency_key("s1", "e1", "hello")
    assert make_idempotency_key("s2", "e1", "hello") != base
    assert make_idempotency_key("s1", "e2", "hello") != base
    assert make_idempotency_key("s1", "e1", "world") != base


def test_idempotency_key_separator_prevents_collisions() -> None:
    # ("ab", "") must not collide with ("a", "b") — the NUL separator guarantees it.
    assert make_idempotency_key("ab", "", "x") != make_idempotency_key("a", "b", "x")


def test_idempotency_key_treats_none_as_empty() -> None:
    assert make_idempotency_key(None, None, "c") == make_idempotency_key("", "", "c")


def test_new_fills_ts_and_key() -> None:
    record = EpisodeRecord.new("a fact", "proj-a", session_id="s", event_id="e")
    assert record.ts, "ts must be auto-stamped"
    assert record.idempotency_key == make_idempotency_key("s", "e", "a fact")


def test_new_respects_explicit_overrides() -> None:
    record = EpisodeRecord.new(
        "a fact", "proj-a", ts="2020-01-01T00:00:00+00:00", idempotency_key="fixed"
    )
    assert record.ts == "2020-01-01T00:00:00+00:00"
    assert record.idempotency_key == "fixed"


def test_to_dict_has_all_fields() -> None:
    data = EpisodeRecord.new("c", "ns").to_dict()
    assert set(data) == set(EPISODE_FIELDS)


def test_from_dict_ignores_unknown_keys() -> None:
    payload = EpisodeRecord.new("c", "ns").to_dict()
    payload["surprise"] = "ignored"
    record = EpisodeRecord.from_dict(payload)
    assert record.content == "c"
    assert record.namespace == "ns"


def test_to_row_from_row_roundtrip() -> None:
    record = EpisodeRecord.new("hello", "proj-a", repo="memrelay").to_dict()
    assert from_row(to_row(record)) == record


def test_to_row_is_order_stable() -> None:
    # Same logical record, different dict insertion order -> identical serialized text.
    record = EpisodeRecord.new("hello", "proj-a").to_dict()
    shuffled = dict(reversed(list(record.items())))
    assert to_row(record) == to_row(shuffled)
