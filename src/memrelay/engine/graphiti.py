"""The memrelay memory engine: store + recall on an embedded graph (E4-S1 / #34).

``MemoryEngine`` is the single object the daemon injects. It exposes exactly the
shared async contract — ``search`` / ``detail`` / ``note`` / ``health`` — plus an
async ``from_config`` factory, and returns only plain, serializable
dicts/strings so results can later cross a socket unchanged.

Wiring (validated by inspection against graphiti-core 0.29.2, see
``docs/e4-engine-notes.md``): one embedded ``GraphDriver`` — resolved from
``cfg.graph.backend`` via the Backend seam (LadybugDB by default, #76) and opened
exactly once — a key-less ``LocalEmbedder``, a strategy-selected ``LLMClient``, and
a no-op key-less cross-encoder (RRF recall never reranks, but Graphiti would
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
from graphiti_core.utils.maintenance.graph_data_operations import clear_data

from memrelay.config import Config, ensure_home, load_config

from .backends import resolve_backend
from .embedder import LocalEmbedder
from .llm.strategy import select_llm_client

if TYPE_CHECKING:
    from graphiti_core.driver.driver import GraphDriver
    from graphiti_core.embedder.client import EmbedderClient
    from graphiti_core.llm_client.client import LLMClient

logger = logging.getLogger(__name__)

_EPISODE_NAME_MAX = 60

#: The ``source_description`` an episode carries when it has neither repo nor agent
#: provenance (see :meth:`MemoryEngine.note`). Excluded from repo matching so a
#: ``forget --repo`` never treats an un-tagged note as belonging to a repo.
_NOTE_SENTINEL = "memrelay-note"


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
    """Persistent memory over an embedded graph backend via graphiti-core."""

    def __init__(self, graphiti: Graphiti, driver: GraphDriver, cfg: Config) -> None:
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

        backend = resolve_backend(cfg.graph.backend)
        driver = await backend.open_driver(cfg)
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

    async def note(
        self,
        content: str,
        namespace: str,
        repo: str | None = None,
        source: str | None = None,
    ) -> str:
        """Store a fact as an episode; returns the episode uuid (or 'Noted.').

        ``source`` is optional agent provenance (E5-S3 #40) — the id of the agent
        that produced the memory (e.g. ``"copilot"`` / ``"claude"``). When it is
        given, the episode's ``source_description`` is a stable, greppable
        ``key=value`` string so a future ``prefer_repo`` tiebreaker can parse repo
        and agent back out (SPEC §4.4): ``repo=<owner/name> agent=<agent>``, or just
        ``agent=<agent>`` when ``repo`` is absent. When ``source`` is falsy the
        description is **byte-identical to the pre-#40 behaviour** (``repo`` alone,
        falling back to ``"memrelay-note"``) so existing callers are unaffected.
        """
        if source:
            tokens = []
            if repo:
                tokens.append(f"repo={repo}")
            tokens.append(f"agent={source}")
            source_description = " ".join(tokens)
        else:
            source_description = repo or "memrelay-note"
        result = await self._graphiti.add_episode(
            name=_episode_name(content),
            episode_body=content,
            source=EpisodeType.message,
            source_description=source_description,
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
        *,
        prefer_agent: str | None = None,
    ) -> dict[str, Any]:
        """Semantic recall across the namespace.

        Returns the daemon wire schema consumed by ``memrelay.mcp.format`` —
        ``{"nodes": [...], "edges": [...], "scores": [...]}`` — where ``scores``
        aligns position-for-position with ``nodes`` (``format_as_map`` pairs
        ``scores[i]`` with ``nodes[i]`` and renders nothing unless ``nodes`` is
        non-empty). Every value is a plain, serializable dict/float so the result
        can cross the daemon socket unchanged.

        Cross-agent unification (E5-S4 #65): memories from every agent in the
        namespace already coexist here — recall is scoped by ``group_ids=[namespace]``
        and never partitioned by agent, so a decision made while driving agent A is
        recalled while driving agent B. The optional, **default-off** ``prefer_agent``
        knob lets a caller lean on agent provenance (parsed from each source episode's
        ``source_description``):

        * ``prefer_agent`` — a soft, sort-stable tiebreaker floating a given agent's
          memories up (mirrors ``prefer_repo``; no score mutation, SPEC §4.4).

        The agent tag is a **soft retrieval signal only — never a hard filter**
        (SPEC §5.3): there is deliberately no agent-exclusive filter, so every agent's
        memories always remain recallable in the namespace. ``prefer_agent`` is
        keyword-only and defaults to ``None``; when it is not supplied the result is
        **byte-identical** to the no-argument path (and no extra graph query runs), so
        existing callers — including the retrieval-eval harness — are unaffected.
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
        if prefer_agent:
            # Agent provenance lives on the source episodes, not the derived entity/edge
            # rows, so resolve it once — only when prefer_agent is set. The default recall
            # path never reaches here and stays byte-identical (no extra graph query).
            edge_episode_uuids = {
                edge.uuid: list(getattr(edge, "episodes", None) or []) for edge in results.edges
            }
            node_uuids = [node["uuid"] for node, _ in node_pairs]
            node_agents, edge_agents = await self._agent_provenance(node_uuids, edge_episode_uuids)
            needle = prefer_agent.strip().lower()
            node_pairs = _boost_agent_pairs(node_pairs, node_agents, needle)
            edges = _boost_agent_edges(edges, edge_agents, needle)
        return {
            "nodes": [node for node, _ in node_pairs],
            "edges": edges,
            "scores": [score for _, score in node_pairs],
        }

    async def _agent_provenance(
        self,
        node_uuids: list[str],
        edge_episode_uuids: dict[str, list[str]],
    ) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """Map each result node/edge uuid to the set of agents that produced it.

        Agent provenance is a property of the ``Episodic`` node (its
        ``source_description``), not of the derived ``Entity`` / ``EntityEdge`` rows a
        recall returns, so this walks back to the source episodes: a node via the
        ``MENTIONS`` edge that links its episode to it, an edge via
        ``EntityEdge.episodes``. Agents are lower-cased for case-insensitive matching
        (mirroring :meth:`_forget_repo`). Only called when ``search`` was given an
        ``agent`` / ``prefer_agent`` knob — the default recall path issues no query here.
        """
        node_agents: dict[str, set[str]] = {}
        edge_agents: dict[str, set[str]] = {}

        if node_uuids:
            records, _, _ = await self._driver.execute_query(
                "MATCH (ep:Episodic)-[:MENTIONS]->(n:Entity) WHERE n.uuid IN $uuids "
                "RETURN n.uuid AS uuid, ep.source_description AS sd",
                uuids=node_uuids,
                routing_="r",
            )
            for record in records:
                agent = _episode_agent(record.get("sd"))
                if agent:
                    node_agents.setdefault(record["uuid"], set()).add(agent.lower())

        episode_uuids = sorted({ep for eps in edge_episode_uuids.values() for ep in eps})
        episode_agent: dict[str, str] = {}
        if episode_uuids:
            records, _, _ = await self._driver.execute_query(
                "MATCH (ep:Episodic) WHERE ep.uuid IN $uuids "
                "RETURN ep.uuid AS uuid, ep.source_description AS sd",
                uuids=episode_uuids,
                routing_="r",
            )
            for record in records:
                agent = _episode_agent(record.get("sd"))
                if agent:
                    episode_agent[record["uuid"]] = agent.lower()
        for edge_uuid, eps in edge_episode_uuids.items():
            agents = {episode_agent[ep] for ep in eps if ep in episode_agent}
            if agents:
                edge_agents[edge_uuid] = agents

        return node_agents, edge_agents

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
        """Report backend/config status and a live probe of the graph connection."""
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
        """Release the graph driver / file lock."""
        close = getattr(self._driver, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    async def forget(
        self,
        *,
        repo: str | None = None,
        namespace: str | None = None,
        dry_run: bool = False,
    ) -> int:
        """Delete memories for a repo or a whole namespace (E9-S1 / #58).

        Exactly one of ``repo`` / ``namespace`` must be given. Returns the number of
        **episodes** deleted (or that *would* be deleted when ``dry_run`` is set) — the
        user-facing unit of memory; derived entities/edges are not counted. This method
        is purely additive: it never mutates ``note`` / ``search`` / ``detail`` data
        beyond the delete it is asked to perform.

        ``namespace`` deletes the entire namespace graph (every node/edge whose
        ``group_id`` equals the namespace). ``repo`` deletes only the episodic nodes
        tagged with that repo (in any namespace); entities shared with surviving
        episodes are preserved. **The delete is irreversible.**
        """
        if bool(repo) == bool(namespace):
            raise ValueError("exactly one of repo or namespace must be provided")
        if namespace:
            return await self._forget_namespace(namespace, dry_run=dry_run)
        assert repo is not None  # narrowed by the guard above
        return await self._forget_repo(repo, dry_run=dry_run)

    async def _forget_namespace(self, namespace: str, *, dry_run: bool) -> int:
        """Drop the whole namespace graph via graphiti-core's ``clear_data``.

        ``namespace`` is matched as an exact ``group_id`` (the opaque partition key
        ``note`` / ``search`` already use verbatim) — no case folding. Returns the count
        of episodes that live (or lived) in the namespace.
        """
        records, _, _ = await self._driver.execute_query(
            "MATCH (e:Episodic) WHERE e.group_id = $group_id RETURN count(e) AS episode_count",
            group_id=namespace,
            routing_="r",
        )
        count = int(records[0]["episode_count"]) if records else 0
        if not dry_run:
            # DETACH DELETEs Entity/Episodic/Community/RelatesToNode_ where group_id
            # matches, plus every incident edge; nothing outside the group is touched.
            await clear_data(self._driver, group_ids=[namespace])
        return count

    async def _forget_repo(self, repo: str, *, dry_run: bool) -> int:
        """Delete the episodic nodes tagged with ``repo`` via ``remove_episode``.

        The repo lives inside each episode's ``source_description`` (verbatim, possibly
        mixed-case), so matching is case-insensitive (``strip().lower()`` on both sides,
        mirroring :func:`memrelay.config._normalize_repo`). ``remove_episode`` cascades
        to edges/entities created solely by a removed episode while preserving entities
        that other episodes still mention. Returns the number of matched episodes.
        """
        target = repo.strip().lower()
        records, _, _ = await self._driver.execute_query(
            "MATCH (e:Episodic) RETURN e.uuid AS uuid, e.source_description AS source_description",
            routing_="r",
        )
        uuids: list[str] = []
        for record in records:
            parsed = _episode_repo(record.get("source_description"))
            if parsed is not None and parsed.strip().lower() == target:
                uuids.append(record["uuid"])
        if not dry_run:
            for uuid in uuids:
                await self._graphiti.remove_episode(uuid)
        return len(uuids)


def _episode_repo(source_description: str | None) -> str | None:
    """Recover the repo an episode was tagged with, or ``None``.

    Inverse of :meth:`MemoryEngine.note`'s ``source_description`` encoding, which is one
    of: ``repo=<repo> agent=<agent>``, ``agent=<agent>``, a bare ``<repo>``, or the
    ``memrelay-note`` sentinel. The two provenance-less forms (agent-only, sentinel)
    yield ``None`` so they never match a ``forget --repo``.
    """
    text = (source_description or "").strip()
    if not text:
        return None
    if "=" in text:
        for token in text.split(" "):
            key, sep, value = token.partition("=")
            if sep and key == "repo":
                return value.strip() or None
        return None
    if text == _NOTE_SENTINEL:
        return None
    return text


def _episode_agent(source_description: str | None) -> str | None:
    """Recover the agent (provider id) an episode was tagged with, or ``None``.

    Sibling of :func:`_episode_repo`, inverting the same ``source_description`` encoding
    :meth:`MemoryEngine.note` writes: ``repo=<repo> agent=<agent>``, ``agent=<agent>``, a
    bare ``<repo>``, or the ``memrelay-note`` sentinel. Only the ``agent=`` token yields a
    value — the repo-only, bare-repo, and sentinel forms (and empty/absent/whitespace) all
    yield ``None`` so an un-attributed episode is never mistaken for one agent's memory. The
    scan is token-order-independent (``note`` writes repo first, but the parser must not rely
    on that).
    """
    text = (source_description or "").strip()
    if "=" not in text:
        return None
    for token in text.split(" "):
        key, sep, value = token.partition("=")
        if sep and key == "agent":
            return value.strip() or None
    return None


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


def _agent_match(uuid: str, provenance: dict[str, set[str]], needle: str) -> bool:
    """True if the item's resolved source-episode agent set contains ``needle``.

    ``provenance`` maps a result node/edge uuid to the (lower-cased) agents that produced
    its source episodes, as resolved by :meth:`MemoryEngine._agent_provenance`; ``needle`` is
    the already-normalized (``strip().lower()``) agent id to match. An unmapped uuid — an
    entity/edge with no parseable agent provenance — never matches.
    """
    return needle in provenance.get(uuid, frozenset())


def _agent_rank(uuid: str, provenance: dict[str, set[str]], needle: str) -> int:
    """0 if the item's source-episode agent set contains ``needle`` (floats up), 1 otherwise."""
    return 0 if _agent_match(uuid, provenance, needle) else 1


def _boost_agent_pairs(
    pairs: list[tuple[dict[str, Any], float | None]], provenance: dict[str, set[str]], needle: str
) -> list[tuple[dict[str, Any], float | None]]:
    """Stable prefer-agent re-rank of (node, score) pairs; keeps them aligned.

    A pure tiebreaker mirroring :func:`_boost_repo_pairs`: pairs whose source-episode agent
    set contains ``needle`` sort to rank ``0`` (float up), the rest to ``1``. ``sorted`` is
    stable, so ties preserve the reranker order and ``scores[i]`` stays paired with
    ``nodes[i]`` — no score is mutated.
    """
    return sorted(pairs, key=lambda pair: _agent_rank(pair[0]["uuid"], provenance, needle))


def _boost_agent_edges(
    edges: list[dict[str, Any]], provenance: dict[str, set[str]], needle: str
) -> list[dict[str, Any]]:
    """Same stable prefer-agent signal applied to the (score-less) edge list."""
    return sorted(edges, key=lambda edge: _agent_rank(edge["uuid"], provenance, needle))
