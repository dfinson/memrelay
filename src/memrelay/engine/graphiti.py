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

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.driver.driver import GraphProvider
from graphiti_core.edges import EntityEdge
from graphiti_core.errors import GroupsEdgesNotFoundError
from graphiti_core.graphiti import Graphiti
from graphiti_core.nodes import EntityNode, EpisodeType
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF
from graphiti_core.utils.maintenance.graph_data_operations import clear_data

from memrelay.config import Config, ensure_home, load_config

from .backends import resolve_backend
from .compaction import (
    EpisodeStat,
    build_summary_content,
    compaction_source_description,
    is_compaction_summary,
    is_degraded,
    select_eligible,
    summary_key,
)
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

#: Wall-clock budget for a single recall graph query (E8-S4 AC2). ``MemoryEngine.search``
#: wraps the Graphiti ``search_`` call in :func:`asyncio.wait_for` with this timeout; if the
#: query overruns, recall degrades to an empty-but-valid result instead of hanging or raising,
#: so a slow graph never wedges the agent. Kept below the daemon client's IPC timeout
#: (``mcp/client.py`` ``DEFAULT_TIMEOUT`` = 5.0s) so this graceful-empty fires first, before the
#: client gives up with a hard error. Injectable per-instance via the ``search_timeout`` ctor
#: argument (the test seam); production uses this default.
_SEARCH_TIMEOUT_SECONDS = 4.0


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

    def __init__(
        self,
        graphiti: Graphiti,
        driver: GraphDriver,
        cfg: Config,
        *,
        search_timeout: float = _SEARCH_TIMEOUT_SECONDS,
    ) -> None:
        self._graphiti = graphiti
        self._driver = driver
        self._cfg = cfg
        self._search_timeout = search_timeout

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
        *,
        last_commit_sha: str | None = None,
        file_change_lines: dict[str, int] | None = None,
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

        ``last_commit_sha`` / ``file_change_lines`` are optional file-refactor provenance
        (E9-S3 #60), populated by the sink only when ``ingest.refactor_invalidation_lines``
        is enabled. When present, ``file=<path>`` tokens (one per touched file) plus a single
        ``sha=<last_commit_sha>`` token are appended to ``source_description`` — making this
        episode's file facts recoverable — and, **before** the new episode is added,
        :meth:`invalidate_file_facts` is called per file so a big-enough refactor supersedes
        that file's prior facts (via temporal edges, never a delete) without the incoming
        fact catching its own invalidation. When both are ``None`` (the zero-config default)
        nothing is appended and no invalidation runs, so behaviour is byte-identical.
        """
        if source:
            tokens = []
            if repo:
                tokens.append(f"repo={repo}")
            tokens.append(f"agent={source}")
            source_description = " ".join(tokens)
        else:
            source_description = repo or "memrelay-note"
        # E9-S3 #60: file-refactor provenance. A path containing a space is skipped — it would
        # break the space-delimited ``key=value`` token grammar the inverse parsers rely on.
        refactor_files = sorted(path for path in (file_change_lines or {}) if " " not in path)
        if refactor_files and last_commit_sha:
            file_tokens = " ".join(f"file={path}" for path in refactor_files)
            source_description = f"{source_description} {file_tokens} sha={last_commit_sha}"
        reference_time = datetime.now(UTC)
        # Supersede prior file facts BEFORE adding the new episode, so the incoming fact is
        # never caught by its own invalidation. Each call self-gates on the threshold, so this
        # is inert (no writes) when the feature is off or the change is below the threshold.
        if last_commit_sha:
            for path in refactor_files:
                await self.invalidate_file_facts(
                    namespace,
                    path,
                    last_commit_sha,
                    change_magnitude=file_change_lines[path],
                    reference_time=reference_time,
                )
        result = await self._graphiti.add_episode(
            name=_episode_name(content),
            episode_body=content,
            source=EpisodeType.message,
            source_description=source_description,
            reference_time=reference_time,
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

        Latency guard (E8-S4 AC2): the graph query is bounded by ``self._search_timeout``
        (:data:`_SEARCH_TIMEOUT_SECONDS`). If it overruns, recall returns an empty-but-valid
        ``{"nodes": [], "edges": [], "scores": []}`` — rendered as the not-found map upstream —
        instead of hanging or raising, so a slow graph never wedges the agent. The ``search_``
        call is atomic, so a timeout yields no partial rows; the empty result is the documented
        "none available" case. A search that completes within the budget is unaffected (its
        result is byte-identical to the un-guarded path), so the retrieval eval still sees the
        full ranking.
        """
        try:
            results = await asyncio.wait_for(
                self._graphiti.search_(
                    query=query,
                    config=COMBINED_HYBRID_SEARCH_RRF,
                    group_ids=[namespace],
                ),
                timeout=self._search_timeout,
            )
        except TimeoutError:
            logger.warning(
                "recall search timed out after %.1fs (namespace=%s); returning empty result",
                self._search_timeout,
                namespace,
            )
            return {"nodes": [], "edges": [], "scores": []}
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

    async def invalidate_file_facts(
        self,
        namespace: str,
        file_path: str,
        new_commit_sha: str,
        *,
        change_magnitude: int,
        reference_time: datetime | None = None,
    ) -> int:
        """Temporally supersede a file's prior facts after a big refactor (E9-S3 #60).

        The deterministic, conservative staleness path (SPEC §5.5). When
        ``change_magnitude`` — the file's changed-line count between its previously-stamped
        commit and ``new_commit_sha`` — meets the configured
        ``ingest.refactor_invalidation_lines`` threshold, every still-valid entity edge
        derived from a *prior* episode that (a) lives in ``namespace`` (matched by
        ``group_id``), (b) carries this ``file_path`` in its ``source_description`` file
        provenance, and (c) was stamped at a *different* commit sha has its bitemporal
        ``expired_at`` / ``invalid_at`` set to ``reference_time`` (now if omitted). Returns
        the number of edges superseded.

        Guarantees, by construction: it is inert — returns 0 and issues no write — unless the
        threshold is a positive value the magnitude meets, so the zero-config default never
        invalidates; it is scoped to the one ``group_id`` (**never crosses a namespace**); it
        only ever touches edges tied to this file's episodes (**never a non-file memory**);
        and it **never deletes** — the fact stays in the graph, fully recallable, merely
        temporally closed. Only the two temporal fields are written; the edge's fact,
        embedding, episodes and attributes are left untouched. This is the single tested
        invalidation entry point; :meth:`note` calls it before adding the new episode so an
        incoming fact never invalidates itself.
        """
        threshold = self._cfg.ingest.refactor_invalidation_lines
        if threshold <= 0 or change_magnitude < threshold:
            return 0
        records, _, _ = await self._driver.execute_query(
            "MATCH (e:Episodic) WHERE e.group_id = $group_id "
            "RETURN e.uuid AS uuid, e.source_description AS source_description",
            group_id=namespace,
            routing_="r",
        )
        stale_episodes = {
            record["uuid"]
            for record in records
            if file_path in _episode_files(record.get("source_description"))
            and _episode_sha(record.get("source_description")) not in (None, new_commit_sha)
        }
        if not stale_episodes:
            return 0
        try:
            edges = await EntityEdge.get_by_group_ids(self._driver, [namespace])
        except GroupsEdgesNotFoundError:
            return 0
        target_uuids = [
            edge.uuid
            for edge in edges
            if edge.expired_at is None and stale_episodes.intersection(edge.episodes or [])
        ]
        if not target_uuids:
            return 0
        ref_time = reference_time if reference_time is not None else datetime.now(UTC)
        await self._driver.execute_query(
            _invalidate_edges_query(self._driver.provider),
            uuids=target_uuids,
            ref_time=ref_time,
        )
        return len(target_uuids)

    async def compact(
        self,
        namespace: str | None = None,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Compact stale, low-value episodes on quality degradation (E9-S2 #59, SPEC §5.5).

        A compaction *pass*: for each namespace whose stale low-value mass has crossed the
        activity-scaled degradation bar, its **oldest, lowest-reference-frequency** episodes are
        folded into **one deterministic extractive summary** and the originals are removed via the
        shared-entity-preserving cascade (:meth:`graphiti_core.Graphiti.remove_episode`, the #58
        primitive ``_forget_repo`` uses) — so the graph shrinks while the gist stays recallable, and
        entities another episode still needs are never orphaned. Busier namespaces compact more
        aggressively (the bar scales with namespace size). ``namespace=None`` sweeps every
        namespace; otherwise only the one given is considered.

        Guarantees, by construction (see :class:`memrelay.config.CompactionConfig`):

        * **Opt-in / byte-identical when off.** With ``compaction.enabled`` at its ``False`` default
          this is an inert no-op — it issues **no** graph query and returns zeroed metrics — and
          ``note`` / ``search`` / ``detail`` / ``health`` are unmodified, so the zero-config
          first-run is unchanged. Compaction only ever happens through an explicit ``compact`` call.
        * **Degradation-driven, not a fixed count.** A namespace is compacted only when it holds at
          least ``min_episodes`` episodes and its eligible (old + low-frequency) episodes reach
          ``ceil(degradation_ratio * episodes)`` (:func:`memrelay.engine.compaction.is_degraded`);
          the newest ``min_episodes`` episodes are always protected, so fresh notes stay.
        * **Deterministic + hermetic.** Selection, the summary key, and the extractive digest are
          pure and offline (no LLM/ML, no wall-clock, no network) — a re-run is byte-identical.
        * **Idempotent / no thrash.** After a pass the eligible set is empty and the summary is
          excluded from the working set, so an immediate re-run is a clean no-op; the deterministic
          per-victim-set summary key plus a pre-existence check means a crash-retry never creates a
          duplicate summary.
        * **Measured before/after (AC4).** Returns per-namespace and aggregate before/after episode,
          edge and entity counts a caller/test can assert on.

        ``dry_run`` computes and reports what *would* be compacted (``eligible``) without writing —
        parity with :meth:`forget`. Returns a structured metrics dict; it never raises for an empty
        or absent namespace.
        """
        if not self._cfg.compaction.enabled:
            # Off ⇒ inert: no driver query, zeroed metrics. Byte-identical to today.
            return {
                "enabled": False,
                "dry_run": dry_run,
                "namespaces": {},
                "episodes_compacted": 0,
                "summaries_added": 0,
            }

        if namespace is not None:
            namespaces = [namespace]
        else:
            records, _, _ = await self._driver.execute_query(
                "MATCH (e:Episodic) RETURN DISTINCT e.group_id AS group_id",
                routing_="r",
            )
            namespaces = sorted(
                {record["group_id"] for record in records if record.get("group_id")}
            )

        per_namespace: dict[str, Any] = {}
        total_compacted = 0
        total_summaries = 0
        for group_id in namespaces:
            metrics = await self._compact_namespace(group_id, dry_run=dry_run)
            per_namespace[group_id] = metrics
            total_compacted += metrics["episodes_compacted"]
            total_summaries += metrics["summaries_added"]

        return {
            "enabled": True,
            "dry_run": dry_run,
            "namespaces": per_namespace,
            "episodes_compacted": total_compacted,
            "summaries_added": total_summaries,
        }

    async def _compact_namespace(self, namespace: str, *, dry_run: bool) -> dict[str, Any]:
        """Run (or, when ``dry_run``, simulate) one namespace's compaction pass and report metrics.

        All Cypher is filtered by ``group_id``, so a pass **never crosses a namespace**. Existing
        compaction summaries (recognized by their ``source_description`` marker) are kept out of the
        working set, which is what makes a re-run a no-op.
        """
        cfg = self._cfg.compaction
        rows, _, _ = await self._driver.execute_query(
            "MATCH (e:Episodic) WHERE e.group_id = $group_id "
            "RETURN e.uuid AS uuid, e.valid_at AS valid_at, e.content AS content, "
            "e.source_description AS source_description, e.entity_edges AS entity_edges",
            group_id=namespace,
            routing_="r",
        )
        episodes_before = len(rows)
        existing_summary_sds = {
            row.get("source_description")
            for row in rows
            if is_compaction_summary(row.get("source_description"))
        }
        stats = [
            EpisodeStat(
                uuid=row["uuid"],
                valid_at=row["valid_at"],
                ref_count=_entity_edge_count(row.get("entity_edges")),
                content=row.get("content") or "",
            )
            for row in rows
            if not is_compaction_summary(row.get("source_description"))
        ]
        episode_count = len(stats)
        eligible = select_eligible(
            stats,
            low_reference_max=cfg.low_reference_max,
            protected_recent=cfg.min_episodes,
        )
        triggered = is_degraded(
            len(eligible),
            episode_count,
            degradation_ratio=cfg.degradation_ratio,
            min_episodes=cfg.min_episodes,
        )
        edges_before = await self._namespace_edge_count(namespace)
        entities_before = await self._namespace_entity_count(namespace)
        metrics: dict[str, Any] = {
            "triggered": triggered,
            "eligible": len(eligible),
            "episodes_before": episodes_before,
            "episodes_after": episodes_before,
            "episodes_compacted": 0,
            "summaries_added": 0,
            "edges_before": edges_before,
            "edges_after": edges_before,
            "entities_before": entities_before,
            "entities_after": entities_before,
        }
        if dry_run or not triggered or not eligible:
            return metrics

        victim_uuids = [stat.uuid for stat in eligible]
        source_description = compaction_source_description(summary_key(victim_uuids))
        if source_description not in existing_summary_sds:
            # Add the summary FIRST, so the gist is present before the originals are removed.
            # ``reference_time`` is not part of the summary's identity (that is the deterministic
            # victim-set key), and a re-run skips recreation, so ``now`` here never breaks the
            # byte-identical-summary guarantee while avoiding any dependence on reading a Kuzu
            # timestamp back out.
            content = build_summary_content([stat.content for stat in eligible])
            await self._graphiti.add_episode(
                name=_episode_name(content),
                episode_body=content,
                source=EpisodeType.message,
                source_description=source_description,
                reference_time=datetime.now(UTC),
                group_id=namespace,
            )
            metrics["summaries_added"] = 1

        for uuid in victim_uuids:
            # Shared-entity-preserving cascade: only edges/entities created solely by this
            # episode are removed; entities another episode still mentions are preserved.
            await self._graphiti.remove_episode(uuid)
        metrics["episodes_compacted"] = len(victim_uuids)

        metrics["episodes_after"] = await self._namespace_episode_count(namespace)
        metrics["edges_after"] = await self._namespace_edge_count(namespace)
        metrics["entities_after"] = await self._namespace_entity_count(namespace)
        return metrics

    async def _namespace_episode_count(self, namespace: str) -> int:
        """Count every ``Episodic`` node in ``namespace`` (summaries included)."""
        records, _, _ = await self._driver.execute_query(
            "MATCH (e:Episodic) WHERE e.group_id = $group_id RETURN count(e) AS episode_count",
            group_id=namespace,
            routing_="r",
        )
        return int(records[0]["episode_count"]) if records else 0

    async def _namespace_entity_count(self, namespace: str) -> int:
        """Count every ``Entity`` node in ``namespace`` (the entity-reclaim metric)."""
        records, _, _ = await self._driver.execute_query(
            "MATCH (n:Entity) WHERE n.group_id = $group_id RETURN count(n) AS entity_count",
            group_id=namespace,
            routing_="r",
        )
        return int(records[0]["entity_count"]) if records else 0

    async def _namespace_edge_count(self, namespace: str) -> int:
        """Count the entity edges in ``namespace`` (the edge-reclaim metric).

        Guarded by ``GroupsEdgesNotFoundError`` exactly like :meth:`invalidate_file_facts`, so a
        namespace that has episodes but no edges yet reports 0 rather than raising.
        """
        try:
            edges = await EntityEdge.get_by_group_ids(self._driver, [namespace])
        except GroupsEdgesNotFoundError:
            return 0
        return len(edges)


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


def _episode_files(source_description: str | None) -> frozenset[str]:
    """Recover the set of file paths a file episode was tagged with (E9-S3 #60).

    Inverse of the ``file=<path>`` tokens :meth:`MemoryEngine.note` appends when file-refactor
    provenance is stamped. A composed episode may touch several files, so *all* ``file=``
    tokens are collected; an episode with none yields an empty set. Paths containing a space
    are never stamped (they would break the token grammar), so they never appear here. The
    scan is token-order-independent and coexists with the ``repo=`` / ``agent=`` / ``sha=``
    tokens that may share the same description.
    """
    text = (source_description or "").strip()
    if "=" not in text:
        return frozenset()
    files: set[str] = set()
    for token in text.split(" "):
        key, sep, value = token.partition("=")
        if sep and key == "file" and value.strip():
            files.add(value.strip())
    return frozenset(files)


def _episode_sha(source_description: str | None) -> str | None:
    """Recover the HEAD commit sha a file episode was stamped at (E9-S3 #60), or ``None``.

    Inverse of the single ``sha=<sha>`` token :meth:`MemoryEngine.note` appends alongside file
    provenance. Absent/empty yields ``None`` so an episode with no stamped sha is never treated
    as belonging to a specific refactor generation (and so is never superseded).
    """
    text = (source_description or "").strip()
    if "=" not in text:
        return None
    for token in text.split(" "):
        key, sep, value = token.partition("=")
        if sep and key == "sha":
            return value.strip() or None
    return None


def _entity_edge_count(value: Any) -> int:
    """Count the entity edges (facts) an episode produced, from its stored ``entity_edges`` cell.

    This is the episode's **reference frequency** — the ``low_reference_max`` compaction knob
    compares against it. graphiti-core stores ``entity_edges`` provider-specifically: Kuzu/LadybugDB
    (memrelay's default) use a native ``STRING[]`` column, so the driver returns a Python list;
    Neptune joins the uuids into one ``|``-delimited string. This tolerates both (and ``None`` /
    empty), so the count is never mistaken for a string length.
    """
    if value is None:
        return 0
    if isinstance(value, (list, tuple)):
        return len(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        for separator in ("|", ","):
            if separator in stripped:
                return len([part for part in stripped.split(separator) if part])
        return 1
    return 0


def _invalidate_edges_query(provider: GraphProvider) -> str:
    """Cypher that temporally supersedes RELATES_TO edges by uuid (E9-S3 #60).

    Sets ONLY the bitemporal ``expired_at`` / ``invalid_at`` — never deletes, and never
    touches fact / embedding / episodes / attributes / reference_time — so a superseded fact
    stays fully recallable, merely temporally closed. LadybugDB/Kuzu store the RELATES_TO fact
    on an intermediary ``RelatesToNode_`` (see ``engine.backends.ladybug_driver``); other
    providers keep it on the relationship itself, so the match shape is provider-specific
    (mirroring graphiti-core's own KUZU branching).
    """
    if provider == GraphProvider.KUZU:
        return (
            "MATCH (n:Entity)-[:RELATES_TO]->(e:RelatesToNode_)-[:RELATES_TO]->(m:Entity) "
            "WHERE e.uuid IN $uuids "
            "SET e.expired_at = $ref_time, e.invalid_at = $ref_time"
        )
    return (
        "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity) "
        "WHERE e.uuid IN $uuids "
        "SET e.expired_at = $ref_time, e.invalid_at = $ref_time"
    )


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
