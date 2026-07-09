"""precision@k / hit@k over ``engine.search`` results (E11-S4 / #21).

Pure standard library — precision@k is simple arithmetic. Node identity is the
(normalized) entity ``name``, never the random per-run UUID, so the metric is stable
across runs. Everything operates on the STRUCTURED wire result
(``{"nodes": [...], ...}``), never on any human-readable rendering.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


def normalize(name: object) -> str:
    """Lowercase/trim an identity so gold labels and retrieved names compare cleanly."""
    return str(name or "").strip().lower()


def ranked_identities(nodes: Sequence[Mapping[str, object]]) -> list[str]:
    """Retrieved node ``name`` identities in rank order, de-duplicated, order-preserving.

    ``engine.search`` returns ``nodes`` already ranked (RRF over BM25 + vector). We
    keep the first occurrence of each identity so top-k reflects distinct entities.
    """
    seen: list[str] = []
    for node in nodes:
        identity = normalize(node.get("name"))
        if identity and identity not in seen:
            seen.append(identity)
    return seen


def precision_at_k(ranked: Sequence[str], gold: Iterable[str], k: int) -> float:
    """|relevant ∩ top-k| / k for one query. Standard precision@k (k in the denominator)."""
    gold_set = {normalize(g) for g in gold}
    if k <= 0 or not gold_set:
        return 0.0
    hits = sum(1 for identity in ranked[:k] if identity in gold_set)
    return hits / k


def hit_at_k(ranked: Sequence[str], gold: Iterable[str], k: int) -> float:
    """1.0 if any relevant identity is within top-k, else 0.0 (a.k.a. success@k)."""
    gold_set = {normalize(g) for g in gold}
    if k <= 0 or not gold_set:
        return 0.0
    return 1.0 if any(identity in gold_set for identity in ranked[:k]) else 0.0


def macro_average(values: Iterable[float]) -> float:
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else 0.0


def summarize(
    results: Sequence[tuple[Sequence[str], Iterable[str]]],
    ks: Sequence[int],
) -> dict[str, float]:
    """Macro-average p@k and hit@k across all ``(ranked_identities, gold)`` query pairs.

    Values are rounded to 6 decimals so the checked-in baseline JSON is byte-stable.
    """
    pairs = [(list(ranked), list(gold)) for ranked, gold in results]
    metrics: dict[str, float] = {}
    for k in ks:
        metrics[f"p@{k}"] = round(
            macro_average(precision_at_k(ranked, gold, k) for ranked, gold in pairs), 6
        )
        metrics[f"hit@{k}"] = round(
            macro_average(hit_at_k(ranked, gold, k) for ranked, gold in pairs), 6
        )
    return metrics
