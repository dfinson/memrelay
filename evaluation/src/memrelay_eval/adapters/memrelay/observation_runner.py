"""Execute the shipped observation compositions behind evaluator-owned seams."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from memrelay_eval.adapters.memrelay.observation import build_observation_identity
from memrelay_eval.adapters.telemetry.semantics import (
    GENAI_DEVELOPMENT_FIELD_MAP,
    OBSERVATION_SENTINEL_ATTRIBUTE_MAP,
)
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.errors import ObservationQualificationError
from memrelay_eval.domain.observation import (
    ObservationBoundary,
    ObservationContract,
    ObservationIdentity,
    ObservationPath,
    SentinelBoundaryRecord,
)


def current_observation_semantic_map() -> dict[str, object]:
    """Return the evaluator-owned map that the active product composition uses."""

    return {
        "genai": GENAI_DEVELOPMENT_FIELD_MAP,
        "observation": OBSERVATION_SENTINEL_ATTRIBUTE_MAP,
    }


def current_observation_source_files(path: ObservationPath) -> tuple[Path, ...]:
    """Resolve the source files of the imported product composition, never caller paths."""

    from memrelay.config import load_config
    from memrelay.daemon.runtime import DaemonRuntime
    from memrelay.daemon.session_discovery import (
        LiveTailCapture,
        RunObserveCapture,
        SessionDiscoveryPoller,
    )
    from memrelay.ingest.graphiti_sink import run_observe, run_tail
    from memrelay.ingest.ingester import Ingester
    from memrelay.ingest.spool import Spool
    from memrelay.mcp.client import DaemonClient
    from memrelay.mcp.server import build_mcp_server
    from memrelay.providers.copilot import CANONICAL_MAPPING, CopilotProvider, mapping_path

    selected_capture = RunObserveCapture if path is ObservationPath.REPLAY else LiveTailCapture
    components: tuple[object, ...] = (
        SessionDiscoveryPoller,
        selected_capture,
        run_observe,
        Spool,
        Ingester,
        DaemonRuntime,
        DaemonClient,
        build_mcp_server,
        CopilotProvider,
        load_config,
    )
    if path is ObservationPath.FILE_WATCH:
        components += (run_tail,)
    files = {
        Path(filename).resolve()
        for component in components
        if (filename := inspect.getsourcefile(component)) is not None
    }
    files.add(Path(mapping_path(CANONICAL_MAPPING)).resolve())
    return tuple(sorted(files))


def load_observation_product_configuration(
    *,
    path: ObservationPath,
    product_config_path: Path,
    workspace: Path,
) -> tuple[Any, dict[str, object]]:
    """Load the selected product configuration into an isolated conformance workspace."""

    from memrelay.config import load_config

    try:
        config_bytes = product_config_path.read_bytes()
    except OSError as error:
        raise ObservationQualificationError("observation_product_config_unreadable") from error
    try:
        config = load_config(
            product_config_path,
            environ={},
            home=str(workspace),
            graph={"backend": "ladybug", "path": str(workspace / "unused-graph.db")},
        )
    except (OSError, ValueError) as error:
        raise ObservationQualificationError("observation_product_config_invalid") from error
    if config.ingest.intake_source != path.value:
        raise ObservationQualificationError("observation_configured_path_mismatch")
    configuration = {
        "product_config_sha256": sha256(config_bytes).hexdigest(),
        "ingest": {
            "enable_boundary": config.ingest.enable_boundary,
            "enable_phase": config.ingest.enable_phase,
            "intake_source": config.ingest.intake_source,
            "max_sessions": config.ingest.max_sessions,
            "session_freshness_s": config.ingest.session_freshness_s,
            "session_poll_interval": config.ingest.session_poll_interval,
        },
    }
    return config, configuration


def current_observation_identity(
    *,
    path: ObservationPath,
    product_config_path: Path,
    runtime_lock_path: Path,
    workspace: Path,
) -> tuple[ObservationIdentity, Any]:
    """Recompute the source, map, effective configuration, and runtime-lock identity."""

    config, configuration = load_observation_product_configuration(
        path=path,
        product_config_path=product_config_path,
        workspace=workspace,
    )
    try:
        runtime_lock = runtime_lock_path.read_bytes()
    except OSError as error:
        raise ObservationQualificationError("observation_runtime_lock_unreadable") from error
    try:
        identity = build_observation_identity(
            source_files=current_observation_source_files(path),
            semantic_map=current_observation_semantic_map(),
            configuration=configuration,
            runtime_lock=runtime_lock,
        )
    except ValueError as error:
        raise ObservationQualificationError("observation_current_identity_invalid") from error
    return identity, config


@dataclass(frozen=True, slots=True)
class ProductObservationRun:
    """Native facts from one actual poll/capture/spool/daemon/MCP execution."""

    native_records: tuple[SentinelBoundaryRecord, ...]
    final_drain_completed: bool
    collector_shutdown_verified: bool
    reconciliation_completed: bool
    authority_conflict: bool
    partial_success: bool
    receipt: bytes


class _RecordingSpool:
    """Keep pre-idempotency ingress facts while delegating persistence to the real spool."""

    def __init__(self, spool: Any) -> None:
        self._spool = spool
        self.pre_idempotency_records: list[dict[str, Any]] = []

    def append(self, record: dict[str, Any]) -> None:
        self.pre_idempotency_records.append(dict(record))
        self._spool.append(record)


class _ObservationBackend:
    """A daemon-injected in-memory backend proving the transport seam without opening a graph."""

    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str | None]] = []

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
        self.notes.append((content, namespace, repo))
        return f"observation-{len(self.notes)}"

    async def search(
        self, query: str, namespace: str, prefer_repo: str | None = None
    ) -> dict[str, Any]:
        matching = [
            (index, content)
            for index, (content, note_namespace, note_repo) in enumerate(self.notes)
            if note_namespace == namespace
            and query in content
            and (prefer_repo is None or note_repo == prefer_repo)
        ]
        return {
            "nodes": [
                {
                    "uuid": f"observation-{index}",
                    "name": "observation sentinel",
                    "summary": content,
                }
                for index, content in matching
            ],
            "edges": [],
            "scores": [1.0 for _ in matching],
        }

    async def detail(self, node_uuid: str, namespace: str) -> dict[str, object]:
        return {
            "node": {"uuid": node_uuid, "name": "observation sentinel", "summary": ""},
            "connected_edges": [],
            "episodes": [],
        }

    async def health(self) -> dict[str, object]:
        return {"status": "running", "backend": "observation-isolated"}


async def _wait_until(predicate: object, *, timeout_seconds: float = 10.0) -> None:
    """Bounded scheduler pumping for deterministic product lifecycle progress."""

    if not callable(predicate):
        raise AssertionError("observation_wait_predicate_invalid")
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise ObservationQualificationError("observation_product_lifecycle_timeout")


def _synthetic_event_lines(*, session_id: str, sentinel_id: str, cwd: Path) -> str:
    """Create a minimal synthetic, non-secret Copilot trace with one open work-unit."""

    events = (
        {
            "type": "session.start",
            "data": {"sessionId": session_id, "context": {"cwd": str(cwd)}},
            "id": f"{session_id}-start",
            "timestamp": "2026-08-11T17:00:00.000Z",
        },
        {
            "type": "user.message",
            "data": {
                "content": f"Synthetic observation sentinel {sentinel_id}.",
                "attachments": [],
            },
            "id": f"{session_id}-message",
            "timestamp": "2026-08-11T17:00:01.000Z",
        },
    )
    return "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events)


def _sentinel_ids_in(
    records: Sequence[Mapping[str, object]], expected_ids: Sequence[str]
) -> set[str]:
    found: set[str] = set()
    for record in records:
        content = record.get("content")
        if isinstance(content, str):
            found.update(identifier for identifier in expected_ids if identifier in content)
    return found


def _records(
    *,
    path: ObservationPath,
    boundary: ObservationBoundary,
    identifiers: Sequence[str],
    expected_sequences: Mapping[str, int],
    observed_at: datetime,
    restart_epoch: int = 1,
) -> tuple[SentinelBoundaryRecord, ...]:
    return tuple(
        SentinelBoundaryRecord(
            path=path,
            boundary=boundary,
            sentinel_id=identifier,
            sequence=expected_sequences[identifier],
            observed_at=observed_at,
            restart_epoch=restart_epoch,
        )
        for identifier in identifiers
    )


def _ordered_identifiers(
    identifiers: Sequence[str] | set[str], expected_sequences: Mapping[str, int]
) -> tuple[str, ...]:
    """Preserve the frozen sentinel order rather than incidental lexical identifier order."""

    return tuple(sorted(identifiers, key=expected_sequences.__getitem__))


async def _exercise_capture_composition(
    *,
    contract: ObservationContract,
    config: Any,
    workspace: Path,
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    int,
    bool,
    tuple[str, ...],
    tuple[str, str | None],
]:
    """Drive the real poller plus selected capture twice, including restart recovery."""

    from memrelay.daemon.session_discovery import (
        LiveTailCapture,
        RunObserveCapture,
        SessionDiscoveryPoller,
    )
    from memrelay.ingest.spool import Spool
    from memrelay.providers.base import SessionRef
    from memrelay.providers.copilot import CopilotProvider

    events_root = workspace / "copilot" / "session-state"
    refs: list[SessionRef] = []
    for sentinel in contract.expected_sentinels:
        session_id = f"observation-{sentinel.sequence:04d}"
        events_path = events_root / session_id / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(
            _synthetic_event_lines(
                session_id=session_id,
                sentinel_id=sentinel.identifier,
                cwd=workspace,
            ),
            encoding="utf-8",
        )
        refs.append(SessionRef(session_id=session_id, agent_id="copilot", path=str(events_path)))

    provider = CopilotProvider(events_root.parent)
    spool_path = config.home_path / "spool" / "spool.db"
    replay_waits = 0
    capture_types: list[str] = []
    active = list(refs)
    captures: dict[str, object] = {}

    async def replay_wait(_: float, stop: asyncio.Event) -> None:
        nonlocal replay_waits
        replay_waits += 1
        await stop.wait()

    with Spool(spool_path) as persisted_spool:
        spool = _RecordingSpool(persisted_spool)

        def capture_factory(ref: SessionRef) -> object:
            if config.ingest.intake_source == ObservationPath.FILE_WATCH.value:
                capture = LiveTailCapture(
                    ref,
                    spool=spool,
                    provider=provider,
                    config=config,
                    namespace_map=config.namespaces.repo_map,
                    interval=config.ingest.session_poll_interval,
                    wait=replay_wait,
                )
                if not isinstance(capture._replay, RunObserveCapture):
                    raise ObservationQualificationError("observation_replay_backstop_missing")
            else:
                capture = RunObserveCapture(
                    ref,
                    spool=spool,
                    provider=provider,
                    config=config,
                    namespace_map=config.namespaces.repo_map,
                    interval=config.ingest.session_poll_interval,
                    wait=replay_wait,
                )
            captures[ref.session_id] = capture
            capture_types.append(type(capture).__name__)
            return capture

        poller = SessionDiscoveryPoller(
            discover=lambda: list(active),
            capture_factory=capture_factory,
            poll_interval=config.ingest.session_poll_interval,
            max_sessions=len(refs),
        )
        try:
            await poller.poll_once()
            await _wait_until(lambda: replay_waits == len(refs))
            if contract.path is ObservationPath.FILE_WATCH:
                await _wait_until(
                    lambda: all(
                        getattr(capture, "_tail_task", None) is not None
                        for capture in captures.values()
                    )
                )

            active.clear()
            await poller.poll_once()

            active.extend(refs)
            await poller.poll_once()
            await _wait_until(lambda: replay_waits == 2 * len(refs))
            active.clear()
            await poller.poll_once()
            final_stats = poller.stats()
        finally:
            await poller.aclose()

        stored_records = tuple(record for _, record in persisted_spool.read_batch())
        pre_idempotency = tuple(spool.pre_idempotency_records)
        final_drain_completed = (
            final_stats["sessions_observed"] == 2 * len(refs)
            and final_stats["active_sessions"] == 0
            and len(stored_records) == len(refs)
            and all(getattr(capture, "_task", None) is None for capture in captures.values())
        )
        if contract.path is ObservationPath.FILE_WATCH:
            final_drain_completed = final_drain_completed and all(
                getattr(capture, "_tail_task", None) is None
                and isinstance(getattr(capture, "_replay", None), RunObserveCapture)
                for capture in captures.values()
            )
    return (
        stored_records,
        pre_idempotency,
        final_stats["sessions_observed"],
        final_drain_completed,
        tuple(capture_types),
        (
            str(stored_records[0]["namespace"]) if stored_records else "",
            stored_records[0].get("repo") if stored_records else None,
        ),
    )


async def _exercise_daemon_and_mcp(
    *,
    config: Any,
    workspace: Path,
    expected_ids: Sequence[str],
    namespace: str,
    repo: str | None,
) -> tuple[set[str], set[str], bool]:
    """Drain through the actual daemon and query it through the actual MCP tool surface."""

    from memrelay.daemon.runtime import DaemonRuntime
    from memrelay.daemon.transport import resolve_endpoint
    from memrelay.mcp.client import DaemonClient
    from memrelay.mcp.server import build_mcp_server

    endpoint = resolve_endpoint(workspace / "daemon")
    backend = _ObservationBackend()
    runtime = DaemonRuntime(config, endpoint, backend=backend)
    await runtime.start()
    serve_task = asyncio.create_task(runtime.serve())
    client = DaemonClient(endpoint, timeout=5.0)
    try:
        await _wait_until(
            lambda: len(backend.notes) >= len(expected_ids),
        )
        daemon_ids = {
            identifier
            for content, _, _ in backend.notes
            for identifier in expected_ids
            if identifier in content
        }
        mcp_ids: set[str] = set()
        for identifier in expected_ids:
            mcp = build_mcp_server(client, context_resolver=lambda: (namespace, repo))
            result = await mcp.call_tool("memory_recall", {"query": identifier})
            blocks = result[0] if isinstance(result, tuple) else result
            text = blocks[0].text if blocks else ""
            if identifier in text:
                mcp_ids.add(identifier)
        health = await client.health()
        return daemon_ids, mcp_ids, health.get("spool_pending") == 0
    finally:
        runtime.request_shutdown()
        await asyncio.wait_for(serve_task, timeout=5.0)


def run_actual_observation_composition(
    *,
    contract: ObservationContract,
    config: Any,
    workspace: Path,
) -> ProductObservationRun:
    """Inject sentinels and collect verified product facts without opening a daemon graph."""

    workspace.mkdir(parents=True, exist_ok=False)
    (
        stored_records,
        pre_records,
        sessions_observed,
        final_drain_completed,
        capture_types,
        (namespace, repo),
    ) = asyncio.run(
        _exercise_capture_composition(contract=contract, config=config, workspace=workspace)
    )
    expected_ids = contract.expected_identifiers
    sentinels = contract.expected_sentinels
    expected_sequences = {sentinel.identifier: sentinel.sequence for sentinel in sentinels}
    spool_ids = _sentinel_ids_in(stored_records, expected_ids)
    pre_ids = _sentinel_ids_in(pre_records, expected_ids)
    daemon_ids, mcp_ids, daemon_drained = asyncio.run(
        _exercise_daemon_and_mcp(
            config=config,
            workspace=workspace,
            expected_ids=expected_ids,
            namespace=namespace,
            repo=repo,
        )
    )
    observed_at = datetime.now(UTC)
    discovery_ids = set(expected_ids) if sessions_observed == 2 * len(expected_ids) else set()
    capture_ids = (
        set(expected_ids)
        if len(capture_types) == 2 * len(expected_ids)
        and all(
            item
            == (
                "RunObserveCapture"
                if contract.path is ObservationPath.REPLAY
                else "LiveTailCapture"
            )
            for item in capture_types
        )
        else set()
    )
    records = (
        _records(
            path=contract.path,
            boundary=ObservationBoundary.DISCOVERY,
            identifiers=_ordered_identifiers(discovery_ids, expected_sequences),
            expected_sequences=expected_sequences,
            observed_at=observed_at,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.CAPTURE,
            identifiers=_ordered_identifiers(capture_ids, expected_sequences),
            expected_sequences=expected_sequences,
            observed_at=observed_at,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.PRE_IDEMPOTENCY,
            identifiers=tuple(
                identifier
                for record in pre_records
                for identifier in expected_ids
                if isinstance(record.get("content"), str) and identifier in record["content"]
            ),
            expected_sequences=expected_sequences,
            observed_at=observed_at,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.SPOOL,
            identifiers=_ordered_identifiers(spool_ids, expected_sequences),
            expected_sequences=expected_sequences,
            observed_at=observed_at,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.DAEMON,
            identifiers=_ordered_identifiers(daemon_ids, expected_sequences),
            expected_sequences=expected_sequences,
            observed_at=observed_at,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.MCP_GRAPH,
            identifiers=_ordered_identifiers(mcp_ids, expected_sequences),
            expected_sequences=expected_sequences,
            observed_at=observed_at,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.TERMINAL_FLUSH,
            identifiers=(
                _ordered_identifiers(spool_ids, expected_sequences) if final_drain_completed else ()
            ),
            expected_sequences=expected_sequences,
            observed_at=observed_at,
        )
    )
    independently_verified = (
        set(expected_ids)
        == discovery_ids
        == capture_ids
        == pre_ids
        == spool_ids
        == daemon_ids
        == mcp_ids
        and final_drain_completed
        and daemon_drained
    )
    receipt = canonical_bytes(
        {
            "schema_version": "1.0.0",
            "artifact_type": "observation_native_receipt",
            "path": contract.path.value,
            "conformance_sha256": contract.identity.conformance_sha256,
            "expected_sentinel_ids": list(expected_ids),
            "source_event_sha256": sorted(
                sha256(path.read_bytes()).hexdigest()
                for path in (workspace / "copilot" / "session-state").glob("*/events.jsonl")
            ),
            "spool_sha256": sha256(
                (config.home_path / "spool" / "spool.db").read_bytes()
            ).hexdigest(),
            "pre_idempotency_ids": sorted(pre_ids),
            "spool_ids": sorted(spool_ids),
            "daemon_ids": sorted(daemon_ids),
            "mcp_ids": sorted(mcp_ids),
            "sessions_observed": sessions_observed,
            "final_drain_completed": final_drain_completed,
            "daemon_drained": daemon_drained,
            "capture_types": list(capture_types),
        }
    )
    return ProductObservationRun(
        native_records=records,
        final_drain_completed=final_drain_completed,
        collector_shutdown_verified=True,
        reconciliation_completed=independently_verified,
        authority_conflict=False,
        partial_success=not independently_verified,
        receipt=receipt,
    )


def receipt_sha256(run: ProductObservationRun) -> str:
    """Return the retained native receipt commitment used by the qualification manifest."""

    return sha256(run.receipt).hexdigest()
