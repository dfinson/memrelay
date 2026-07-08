"""The memrelay memory engine: store + recall on embedded Kuzu (E4-S1 / #34).

``MemoryEngine`` is the single object the daemon injects. It exposes exactly the
shared async contract — ``search`` / ``detail`` / ``note`` / ``health`` — plus an
async ``from_config`` factory, and returns only plain, serializable
dicts/strings so results can later cross a socket unchanged.

Wiring (validated by inspection against graphiti-core 0.29.2, see
``docs/e4-engine-notes.md``): one embedded ``KuzuDriver`` opened READ_WRITE
exactly once, a key-less ``LocalEmbedder``, a strategy-selected ``LLMClient``,
and a no-op key-less cross-encoder (RRF recall never reranks, but Graphiti would
otherwise default to the OpenAI reranker which needs a key).
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.graphiti import Graphiti
from graphiti_core.nodes import EntityNode, EpisodeType
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF

from memrelay.config import Config, ensure_home, load_config

from .embedder import LocalEmbedder
from .kuzu_backend import open_kuzu_driver
from .llm.strategy import select_llm_client

if TYPE_CHECKING:
    from graphiti_core.driver.kuzu_driver import KuzuDriver
    from graphiti_core.embedder.client import EmbedderClient
    from graphiti_core.llm_client.client import LLMClient

logger = logging.getLogger(__name__)

_EPISODE_NAME_MAX = 60


class PassthroughCrossEncoder(CrossEncoderClient):
    """Key-less no-op reranker.

    memrelay recalls with RRF recipes, which never invoke ``rank``; this exists
    solely so ``Graphiti(...)`` does not fall back to ``OpenAIRerankerClient``
    (which requires ``OPENAI_API_KEY``) when no cross-encoder is supplied.
    """

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        # Preserve incoming order with monotonically decreasing scores.
        return [(passage, 1.0 - index * 1e-6) for index, passage in enumerate(passages)]


def _episode_name(content: str) -> str:
    first_line = content.strip().splitlines()[0] if content.strip() else "note"
    if len(first_line) > _EPISODE_NAME_MAX:
        return first_line[: _EPISODE_NAME_MAX - 1].rstrip() + "\u2026"
    return first_line or "note"


def build_embedder(cfg: Config) -> EmbedderClient:
    """Select the embedder from config: local fastembed (default) or OpenAI byo-key."""
    provider = (cfg.embeddings.provider or "local").lower()
    if provider == "local":
        return LocalEmbedder(
            model_name=cfg.embeddings.model,
            cache_dir=cfg.home_path / "models",
        )
    if provider == "openai":
        from .llm.byo_key import build_openai_embedder

        return build_openai_embedder(cfg)
    raise ValueError(f"unknown embeddings provider: {cfg.embeddings.provider!r}")


@dataclass
class _EngineParts:
    graphiti: Graphiti
    driver: Any
    cfg: Config


class MemoryEngine:
    """Persistent memory over an embedded Kuzu graph via graphiti-core."""

    def __init__(self, graphiti: Graphiti, driver: KuzuDriver, cfg: Config) -> None:
        self._graphiti = graphiti
        self._driver = driver
        self._cfg = cfg

    @classmethod
    async def from_config(
        cls,
        cfg: Config | None = None,
        *,
        llm_client: LLMClient | None = None,
        embedder: EmbedderClient | None = None,
        cross_encoder: CrossEncoderClient | None = None,
    ) -> MemoryEngine:
        """Build a ``MemoryEngine`` from a :class:`~memrelay.config.Config`.

        The ``llm_client`` / ``embedder`` / ``cross_encoder`` overrides exist so
        the hermetic gate can inject a deterministic mock LLM (and, if needed, a
        fake embedder) without any network or API key.
        """
        if cfg is None:
            cfg = load_config()
        ensure_home(cfg)

        driver = await open_kuzu_driver(cfg.graph_path)
        resolved_embedder = embedder or build_embedder(cfg)
        resolved_llm = llm_client or select_llm_client(cfg)
        resolved_reranker = cross_encoder or PassthroughCrossEncoder()

        graphiti = Graphiti(
            graph_driver=driver,
            llm_client=resolved_llm,
            embedder=resolved_embedder,
            cross_encoder=resolved_reranker,
        )
        return cls(graphiti=graphiti, driver=driver, cfg=cfg)

    async def note(self, content: str, namespace: str, repo: str | None = None) -> str:
        """Store a fact as an episode; returns the episode uuid (or 'Noted.')."""
        result = await self._graphiti.add_episode(
            name=_episode_name(content),
            episode_body=content,
            source=EpisodeType.message,
            source_description=repo or "memrelay-note",
            reference_time=datetime.now(UTC),
            group_id=namespace,
        )
        episode = getattr(result, "episode", None)
        episode_uuid = getattr(episode, "uuid", None)
        return episode_uuid or "Noted."

    async def search(
        self,
        query: str,
        namespace: str,
        prefer_repo: str | None = None,
    ) -> dict[str, Any]:
        """Semantic recall across the namespace.

        Returns the daemon wire schema consumed by ``memrelay.mcp.format`` —
        ``{"nodes": [...], "edges": [...], "scores": [...]}`` — where ``scores``
        aligns position-for-position with ``nodes`` (``format_as_map`` pairs
        ``scores[i]`` with ``nodes[i]`` and renders nothing unless ``nodes`` is
        non-empty). Every value is a plain, serializable dict/float so the result
        can cross the daemon socket unchanged.
        """
        results = await self._graphiti.search_(
            query=query,
            config=COMBINED_HYBRID_SEARCH_RRF,
            group_ids=[namespace],
        )
        node_pairs: list[tuple[dict[str, Any], float | None]] = [
            (
                {
                    "uuid": node.uuid,
                    "name": node.name,
                    "summary": getattr(node, "summary", None),
                },
                score,
            )
            for node, score in _zip_scores(results.nodes, results.node_reranker_scores)
        ]
        edges: list[dict[str, Any]] = [
            {
                "uuid": edge.uuid,
                "name": edge.name,
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "fact": edge.fact,
            }
            for edge in results.edges
        ]
        if prefer_repo:
            # Keep nodes and their scores aligned by sorting the pairs jointly.
            node_pairs = _boost_repo_pairs(node_pairs, prefer_repo)
            edges = _boost_repo_edges(edges, prefer_repo)
        return {
            "nodes": [node for node, _ in node_pairs],
            "edges": edges,
            "scores": [score for _, score in node_pairs],
        }

    async def detail(self, node_uuid: str, namespace: str) -> dict[str, Any]:
        """Fetch a single node plus its connected facts and episodes.

        Returns the daemon wire schema consumed by ``memrelay.mcp.format`` —
        ``{"node": {...} | None, "connected_edges": [...], "episodes": [...]}``.
        ``format_detail`` renders "Entity not found." when ``node`` is falsy, so
        an unknown uuid resolves to ``node=None`` (with empty lists) rather than
        raising.
        """
        try:
            node = await EntityNode.get_by_uuid(self._driver, node_uuid)
        except Exception as exc:  # noqa: BLE001 - not-found and driver errors both mean "no detail"
            logger.debug("detail(%s) lookup failed: %s", node_uuid, exc)
            return {"node": None, "connected_edges": [], "episodes": []}

        connected_edges: list[dict[str, Any]] = []
        episodes: list[dict[str, Any]] = []
        try:
            centered = await self._graphiti.search_(
                query=node.name or "",
                config=COMBINED_HYBRID_SEARCH_RRF,
                group_ids=[namespace],
                center_node_uuid=node_uuid,
            )
            connected_edges = [
                {
                    "uuid": edge.uuid,
                    "name": edge.name,
                    "source_node_uuid": edge.source_node_uuid,
                    "target_node_uuid": edge.target_node_uuid,
                    "fact": edge.fact,
                }
                for edge in centered.edges
            ]
            episodes = [
                {
                    "uuid": episode.uuid,
                    "name": getattr(episode, "name", "") or "",
                    "content": getattr(episode, "content", "") or "",
                }
                for episode in getattr(centered, "episodes", None) or []
            ]
        except Exception as exc:  # noqa: BLE001 - connected-edge/episode recall is best effort
            logger.debug("detail(%s) connected search failed: %s", node_uuid, exc)

        return {
            "node": {
                "uuid": node.uuid,
                "name": node.name,
                "summary": getattr(node, "summary", None),
                "labels": list(getattr(node, "labels", []) or []),
                "group_id": node.group_id,
                "created_at": node.created_at.isoformat() if node.created_at else None,
                "attributes": getattr(node, "attributes", {}) or {},
            },
            "connected_edges": connected_edges,
            "episodes": episodes,
        }

    async def health(self) -> dict[str, Any]:
        """Report backend/config status and a live probe of the Kuzu connection."""
        status = "ok"
        error: str | None = None
        try:
            await self._driver.execute_query("RETURN 1 AS ok")
        except Exception as exc:  # noqa: BLE001 - surface any driver failure in the report
            status = "error"
            error = str(exc)

        report: dict[str, Any] = {
            "status": status,
            "backend": self._cfg.graph.backend,
            "graph_path": str(self._cfg.graph_path),
            "llm_strategy": self._cfg.llm.strategy,
            "embeddings_provider": self._cfg.embeddings.provider,
            "embeddings_model": self._cfg.embeddings.model,
        }
        if error is not None:
            report["error"] = error
        return report

    async def close(self) -> None:
        """Release the Kuzu driver / file lock."""
        close = getattr(self._driver, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result


def _zip_scores(items: list[Any], scores: list[float] | None) -> list[tuple[Any, float | None]]:
    scores = scores or []
    paired: list[tuple[Any, float | None]] = []
    for index, item in enumerate(items):
        paired.append((item, scores[index] if index < len(scores) else None))
    return paired


def _repo_rank(item: dict[str, Any], needle: str) -> int:
    """0 if the item mentions ``needle`` (floats up), 1 otherwise."""
    haystack = " ".join(str(item.get(key, "") or "") for key in ("name", "fact", "summary")).lower()
    return 0 if needle in haystack else 1


def _boost_repo_pairs(
    pairs: list[tuple[dict[str, Any], float | None]], prefer_repo: str
) -> list[tuple[dict[str, Any], float | None]]:
    """Stable best-effort re-rank of (node, score) pairs; keeps them aligned.

    repo is not a first-class node property in graphiti's model, so this is an
    intentionally soft signal (substring match over name/summary). ``sorted`` is
    stable, so ties preserve the reranker order and ``scores[i]`` stays paired
    with ``nodes[i]``.
    """
    needle = prefer_repo.lower()
    return sorted(pairs, key=lambda pair: _repo_rank(pair[0], needle))


def _boost_repo_edges(edges: list[dict[str, Any]], prefer_repo: str) -> list[dict[str, Any]]:
    """Same soft prefer-repo signal applied to the (score-less) edge list."""
    needle = prefer_repo.lower()
    return sorted(edges, key=lambda edge: _repo_rank(edge, needle))
