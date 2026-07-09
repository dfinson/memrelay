"""Seeded synthetic-session generator with labeled gold queries (E11-S4 / #21).

A fixed ``seed`` produces a byte-identical corpus: a set of synthetic "sessions"
(each a bundle of fact sentences) plus a labeled query per topic, where the label is
the set of relevant node identities (entity names) that recall SHOULD surface in the
top-k. Standard library only (``random`` + ``json``).

Design (mirrors the distinctive-token trick in
``tests/integration/test_cross_agent_recall.py``): every topic owns two rare,
invented anchor tokens with no semantic neighbours, so recall is decided by the
anchor rather than by ambiguous real-word overlap. Topics share a small pool of
domain words, so same-domain topics act as mutual distractors and precision is not
trivially 1.0. The mock LLM extracts each anchor and domain word as an entity; the
gold-relevant set for a topic's query is that topic's two anchors.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

# Rare, invented anchor tokens (>= 2 per topic). Distinctive terms keep recall
# deterministic under a lexical embedder (a real word like "cache" has neighbours).
_ANCHORS: tuple[str, ...] = (
    "Zephyr",
    "Quasar",
    "Nimbus",
    "Vantik",
    "Obsidian",
    "Halcyon",
    "Marlin",
    "Cinder",
    "Thorne",
    "Wrenlow",
    "Larkspur",
    "Basalt",
    "Cobalt",
    "Driftwood",
    "Emberly",
    "Fathom",
    "Glimmer",
    "Harrow",
    "Indigo",
    "Juniper",
    "Kestrel",
    "Lumen",
    "Meridian",
    "Novato",
    "Onyxia",
    "Pallas",
    "Quillon",
    "Riven",
    "Sable",
    "Tamarind",
    "Umbra",
    "Verdant",
    "Wispel",
    "Yarrow",
    "Zenith",
    "Aster",
    "Bramble",
    "Cypher",
    "Dovetail",
    "Ecliptic",
)

# Shared domain words: assigned round-robin so several topics collide on a domain
# and become each other's distractors under recall.
_DOMAINS: tuple[str, ...] = (
    "authentication",
    "monitoring",
    "caching",
    "deployment",
    "logging",
    "scheduling",
    "encryption",
    "indexing",
    "replication",
    "throttling",
)

_SUBJECTS: tuple[str, ...] = ("service", "module", "pipeline", "subsystem", "component", "layer")
_VERBS: tuple[str, ...] = ("handles", "manages", "coordinates", "governs", "drives", "owns")
_FILLERS: tuple[str, ...] = (
    "for the platform",
    "across every region",
    "under sustained load",
    "during nightly runs",
    "for downstream consumers",
    "in the request hot path",
)


@dataclass(frozen=True)
class GoldQuery:
    """A labeled query and the set of node identities (entity names) it should recall."""

    query: str
    relevant: tuple[str, ...]


@dataclass(frozen=True)
class Session:
    """A synthetic session: a bundle of fact sentences noted into one namespace."""

    session_id: str
    facts: tuple[str, ...]


@dataclass(frozen=True)
class Corpus:
    """The full generated corpus: sessions to ingest, gold queries, and mock-LLM vocab."""

    seed: int
    namespace: str
    n_topics: int
    facts_per_topic: int
    sessions: tuple[Session, ...]
    queries: tuple[GoldQuery, ...]
    vocab: tuple[str, ...]

    def all_facts(self) -> list[str]:
        return [fact for session in self.sessions for fact in session.facts]

    def to_canonical_json(self) -> str:
        """Stable serialization used to assert byte-identical generation for a fixed seed."""
        payload = {
            "seed": self.seed,
            "namespace": self.namespace,
            "n_topics": self.n_topics,
            "facts_per_topic": self.facts_per_topic,
            "sessions": [
                {"session_id": session.session_id, "facts": list(session.facts)}
                for session in self.sessions
            ],
            "queries": [
                {"query": query.query, "relevant": list(query.relevant)} for query in self.queries
            ],
            "vocab": list(self.vocab),
        }
        return json.dumps(payload, sort_keys=True, indent=2)


def generate(
    *,
    seed: int = 1729,
    n_topics: int = 12,
    facts_per_topic: int = 2,
    namespace: str = "eval",
) -> Corpus:
    """Build a deterministic corpus. A fixed ``seed`` yields a byte-identical result."""
    if n_topics < 1 or facts_per_topic < 1:
        raise ValueError("n_topics and facts_per_topic must be >= 1")
    if n_topics * 2 > len(_ANCHORS):
        raise ValueError(f"need {n_topics * 2} anchors but only {len(_ANCHORS)} are defined")

    rng = random.Random(seed)
    anchors = list(_ANCHORS)
    rng.shuffle(anchors)

    sessions: list[Session] = []
    queries: list[GoldQuery] = []
    used_anchors: list[str] = []

    for index in range(n_topics):
        primary = anchors[2 * index]
        secondary = anchors[2 * index + 1]
        domain = _DOMAINS[index % len(_DOMAINS)]
        subject = rng.choice(_SUBJECTS)
        used_anchors.extend((primary, secondary))

        facts: list[str] = []
        for _ in range(facts_per_topic):
            verb = rng.choice(_VERBS)
            filler = rng.choice(_FILLERS)
            support = rng.choice(_SUBJECTS)
            facts.append(
                f"The {primary} {subject} {verb} {domain} {filler}; "
                f"the {secondary} {support} keeps it consistent."
            )

        sessions.append(Session(session_id=f"syn-{index:02d}", facts=tuple(facts)))
        # Query wording deliberately differs from the facts (different verb/frame) so the
        # match is not a trivial exact-substring hit, while both anchors keep it decidable.
        query_verb = rng.choice(_VERBS)
        queries.append(
            GoldQuery(
                query=f"which {subject} {query_verb} {domain} using {primary} and {secondary}",
                relevant=(primary.lower(), secondary.lower()),
            )
        )

    # Entities the mock LLM should recognize: every anchor plus the domain words.
    vocab = tuple(used_anchors) + _DOMAINS
    return Corpus(
        seed=seed,
        namespace=namespace,
        n_topics=n_topics,
        facts_per_topic=facts_per_topic,
        sessions=tuple(sessions),
        queries=tuple(queries),
        vocab=vocab,
    )
