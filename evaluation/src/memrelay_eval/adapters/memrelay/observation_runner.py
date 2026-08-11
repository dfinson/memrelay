"""Execute the shipped observation compositions behind evaluator-owned seams."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from memrelay_eval.adapters.memrelay.observation import build_observation_identity
from memrelay_eval.adapters.telemetry.semantics import (
    GENAI_DEVELOPMENT_FIELD_MAP,
    OBSERVATION_SENTINEL_ATTRIBUTE_MAP,
    SpanClass,
    TelemetryAttemptEmitter,
    TelemetryContext,
    TelemetrySpan,
)
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.errors import ObservationQualificationError
from memrelay_eval.domain.identity import identity_for_span_class
from memrelay_eval.domain.observation import (
    ObservationBoundary,
    ObservationContract,
    ObservationFailureReason,
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
    telemetry_spans: tuple[TelemetrySpan, ...]
    evidence_failure_reasons: tuple[ObservationFailureReason, ...]


class ObservationTelemetryCollector:
    """Retain spans emitted during one observation composition before shutdown."""

    def __init__(self) -> None:
        self._spans: list[TelemetrySpan] = []
        self._shutdown_verified = False

    def emit_span(self, span: TelemetrySpan) -> None:
        if self._shutdown_verified:
            raise ObservationQualificationError("observation_telemetry_emitted_after_shutdown")
        self._spans.append(span)

    @property
    def spans(self) -> tuple[TelemetrySpan, ...]:
        return tuple(self._spans)

    def shutdown(self) -> bool:
        self._shutdown_verified = True
        return True


@dataclass(frozen=True, slots=True)
class _LiveTailDelivery:
    """Value-safe delivery facts read by the real live-tail source."""

    identifiers: tuple[str, ...]
    observed_at: Mapping[str, datetime]
    failed: bool


class _RecordingTailSource:
    """Proxy a live source while retaining only sentinel IDs and source timestamps."""

    def __init__(self, source: Any, expected_ids: Sequence[str]) -> None:
        self._source = source
        self._expected_ids = tuple(expected_ids)
        self._entered: Any | None = None
        self._identifiers: set[str] = set()
        self._observed_at: dict[str, datetime] = {}
        self.failed = False
        self.exhausted = False

    async def __aenter__(self) -> _RecordingTailSource:
        try:
            self._entered = await self._source.__aenter__()
        except Exception:
            self.failed = True
            raise
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return bool(await self._source.__aexit__(*exc))

    def __aiter__(self) -> Any:
        return self._records()

    async def _records(self) -> Any:
        assert self._entered is not None
        try:
            async for record in self._entered:
                payload = getattr(record, "payload", None)
                if isinstance(payload, str):
                    self._retain(payload)
                yield record
            self.exhausted = True
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failed = True
            raise

    def _retain(self, payload: str) -> None:
        try:
            source = json.loads(payload)
        except (TypeError, ValueError):
            return
        if not isinstance(source, dict):
            return
        data = source.get("data")
        content = data.get("content") if isinstance(data, dict) else None
        timestamp = _parse_observed_at(source.get("timestamp"))
        if not isinstance(content, str) or timestamp is None:
            return
        for identifier in self._expected_ids:
            if identifier in content:
                self._identifiers.add(identifier)
                self._observed_at.setdefault(identifier, timestamp)

    def delivery(self) -> _LiveTailDelivery:
        return _LiveTailDelivery(
            identifiers=tuple(sorted(self._identifiers)),
            observed_at=dict(self._observed_at),
            failed=self.failed,
        )


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


def _synthetic_event_lines(
    *,
    session_id: str,
    sentinel_id: str,
    cwd: Path,
    observed_at: datetime,
) -> str:
    """Create a minimal synthetic, non-secret Copilot trace with one open work-unit."""

    timestamp = observed_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
    events = (
        {
            "type": "session.start",
            "data": {"sessionId": session_id, "context": {"cwd": str(cwd)}},
            "id": f"{session_id}-start",
            "timestamp": timestamp,
        },
        {
            "type": "user.message",
            "data": {
                "content": f"Synthetic observation sentinel {sentinel_id}.",
                "attachments": [],
            },
            "id": f"{session_id}-message",
            "timestamp": timestamp,
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
    observed_at: Mapping[str, datetime],
    restart_epoch: int = 1,
) -> tuple[SentinelBoundaryRecord, ...]:
    return tuple(
        SentinelBoundaryRecord(
            path=path,
            boundary=boundary,
            sentinel_id=identifier,
            sequence=expected_sequences[identifier],
            observed_at=observed_at[identifier],
            restart_epoch=restart_epoch,
        )
        for identifier in identifiers
        if identifier in observed_at
    )


def _ordered_identifiers(
    identifiers: Sequence[str] | set[str], expected_sequences: Mapping[str, int]
) -> tuple[str, ...]:
    """Preserve the frozen sentinel order rather than incidental lexical identifier order."""

    return tuple(sorted(identifiers, key=expected_sequences.__getitem__))


def _source_event_times(contract: ObservationContract) -> dict[str, datetime]:
    """Place each injected source sentinel strictly inside its frozen window."""

    interval = (contract.deadline_at - contract.window_started_at) / (
        len(contract.expected_sentinels) + 1
    )
    return {
        sentinel.identifier: contract.window_started_at + interval * sentinel.sequence
        for sentinel in contract.expected_sentinels
    }


def _parse_observed_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _retained_record_times(
    records: Sequence[Mapping[str, object]], expected_ids: Sequence[str]
) -> dict[str, datetime]:
    """Extract value-safe source times retained by real product episode records."""

    observed_at: dict[str, datetime] = {}
    for record in records:
        content = record.get("content")
        timestamp = _parse_observed_at(record.get("ts"))
        if not isinstance(content, str) or timestamp is None:
            continue
        for identifier in expected_ids:
            if identifier in content:
                observed_at.setdefault(identifier, timestamp)
    return observed_at


async def _exercise_capture_composition(
    *,
    contract: ObservationContract,
    config: Any,
    workspace: Path,
    tail_source_factory: Callable[[Any], Any] | None,
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    int,
    bool,
    tuple[str, ...],
    tuple[str, str | None],
    _LiveTailDelivery,
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
    source_times = _source_event_times(contract)
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
                observed_at=source_times[sentinel.identifier],
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
    tail_probes: list[_RecordingTailSource] = []
    tail_captures: list[tuple[_RecordingTailSource, object]] = []
    tail_task_failed = False

    async def replay_wait(_: float, stop: asyncio.Event) -> None:
        nonlocal replay_waits
        replay_waits += 1
        await stop.wait()

    with Spool(spool_path) as persisted_spool:
        spool = _RecordingSpool(persisted_spool)

        def capture_factory(ref: SessionRef) -> object:
            if config.ingest.intake_source == ObservationPath.FILE_WATCH.value:
                source = (
                    tail_source_factory(ref)
                    if tail_source_factory is not None
                    else provider.make_filewatch_source(
                        ref.session_id,
                        path=str(ref.path),
                        start_at="beginning",
                    )
                )
                probe = _RecordingTailSource(source, contract.expected_identifiers)
                tail_probes.append(probe)
                capture = LiveTailCapture(
                    ref,
                    spool=spool,
                    provider=provider,
                    config=config,
                    namespace_map=config.namespaces.repo_map,
                    interval=config.ingest.session_poll_interval,
                    wait=replay_wait,
                    tail_source_factory=lambda _ref, source=probe: source,
                )
                if not isinstance(capture._replay, RunObserveCapture):
                    raise ObservationQualificationError("observation_replay_backstop_missing")
                tail_captures.append((probe, capture))
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

        async def await_tail_delivery(
            entries: Sequence[tuple[_RecordingTailSource, object]],
        ) -> None:
            nonlocal tail_task_failed
            if not entries:
                return
            probes = tuple(probe for probe, _ in entries)
            try:
                await _wait_until(
                    lambda: set(contract.expected_identifiers).issubset(
                        {
                            identifier
                            for probe in probes
                            for identifier in probe.delivery().identifiers
                        }
                    )
                    or all(probe.failed or probe.exhausted for probe in probes),
                    timeout_seconds=2.0,
                )
            except ObservationQualificationError:
                # A healthy-but-silent tail is retained as failed path evidence, not raised
                # away before a typed unqualified decision can be persisted.
                pass
            tail_task_failed = tail_task_failed or any(
                probe.failed
                or (
                    (task := getattr(capture, "_tail_task", None)) is None
                    or (task.done() and not probe.exhausted)
                )
                for probe, capture in entries
            )

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
                await await_tail_delivery(tuple(tail_captures))

            active.clear()
            await poller.poll_once()

            active.extend(refs)
            await poller.poll_once()
            await _wait_until(lambda: replay_waits == 2 * len(refs))
            if contract.path is ObservationPath.FILE_WATCH:
                await await_tail_delivery(tuple(tail_captures[len(refs) :]))
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
        tail_identifiers: set[str] = set()
        tail_observed_at: dict[str, datetime] = {}
        tail_failed = tail_task_failed
        for probe in tail_probes:
            delivery = probe.delivery()
            tail_identifiers.update(delivery.identifiers)
            for identifier, observed_at in delivery.observed_at.items():
                tail_observed_at.setdefault(identifier, observed_at)
            tail_failed = tail_failed or delivery.failed
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
        _LiveTailDelivery(
            identifiers=_ordered_identifiers(
                tail_identifiers,
                {sentinel.identifier: sentinel.sequence for sentinel in contract.expected_sentinels},
            ),
            observed_at=tail_observed_at,
            failed=tail_failed,
        ),
    )


async def _exercise_daemon_and_mcp(
    *,
    config: Any,
    workspace: Path,
    expected_ids: Sequence[str],
    namespace: str,
    repo: str | None,
    path: ObservationPath,
    observed_at: Mapping[str, datetime],
    telemetry: TelemetryAttemptEmitter,
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
                timestamp = observed_at.get(identifier)
                if timestamp is not None:
                    telemetry.record(
                        SpanClass.DAEMON_DISPATCH,
                        started_at=timestamp,
                        ended_at=timestamp,
                        attributes={
                            "sentinel_id": identifier,
                            "sentinel_sequence": expected_ids.index(identifier) + 1,
                            "observation_path": path.value,
                            "restart_epoch": 1,
                        },
                    )
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
    tail_source_factory: Callable[[Any], Any] | None = None,
    telemetry_collector_factory: Callable[[], ObservationTelemetryCollector] | None = None,
) -> ProductObservationRun:
    """Inject sentinels and collect product records plus composition-emitted telemetry."""

    workspace.mkdir(parents=True, exist_ok=False)
    (
        stored_records,
        pre_records,
        sessions_observed,
        final_drain_completed,
        capture_types,
        (namespace, repo),
        live_tail,
    ) = asyncio.run(
        _exercise_capture_composition(
            contract=contract,
            config=config,
            workspace=workspace,
            tail_source_factory=tail_source_factory,
        )
    )
    expected_ids = contract.expected_identifiers
    sentinels = contract.expected_sentinels
    expected_sequences = {sentinel.identifier: sentinel.sequence for sentinel in sentinels}
    spool_ids = _sentinel_ids_in(stored_records, expected_ids)
    pre_ids = _sentinel_ids_in(pre_records, expected_ids)
    observed_at = _retained_record_times(stored_records, expected_ids)
    for identifier, timestamp in _retained_record_times(pre_records, expected_ids).items():
        observed_at.setdefault(identifier, timestamp)
    collector = (
        telemetry_collector_factory()
        if telemetry_collector_factory is not None
        else ObservationTelemetryCollector()
    )
    telemetry = TelemetryAttemptEmitter(
        _observation_telemetry_context(contract),
        collector,
    )
    try:
        daemon_ids, mcp_ids, daemon_drained = asyncio.run(
            _exercise_daemon_and_mcp(
                config=config,
                workspace=workspace,
                expected_ids=expected_ids,
                namespace=namespace,
                repo=repo,
                path=contract.path,
                observed_at=observed_at,
                telemetry=telemetry,
            )
        )
    finally:
        collector_shutdown_verified = collector.shutdown()
    discovery_ids = set(expected_ids) if sessions_observed == 2 * len(expected_ids) else set()
    capture_composition_verified = len(capture_types) == 2 * len(expected_ids) and all(
        item
        == (
            "RunObserveCapture"
            if contract.path is ObservationPath.REPLAY
            else "LiveTailCapture"
        )
        for item in capture_types
    )
    live_tail_delivery_verified = (
        set(live_tail.identifiers) == set(expected_ids) and not live_tail.failed
    )
    capture_ids = (
        set(expected_ids)
        if capture_composition_verified
        and (
            contract.path is ObservationPath.REPLAY
            or live_tail_delivery_verified
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
            boundary=ObservationBoundary.LIVE_TAIL,
            identifiers=live_tail.identifiers,
            expected_sequences=expected_sequences,
            observed_at=live_tail.observed_at,
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
        and (
            contract.path is ObservationPath.REPLAY
            or live_tail_delivery_verified
        )
    )
    evidence_failure_reasons: list[ObservationFailureReason] = []
    if contract.path is ObservationPath.FILE_WATCH:
        if live_tail.failed:
            evidence_failure_reasons.append(ObservationFailureReason.LIVE_TAIL_DELIVERY_FAILED)
        if set(live_tail.identifiers) != set(expected_ids):
            evidence_failure_reasons.append(ObservationFailureReason.LIVE_TAIL_DELIVERY_MISSING)
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
            "live_tail_ids": list(live_tail.identifiers),
            "live_tail_failed": live_tail.failed,
            "sessions_observed": sessions_observed,
            "final_drain_completed": final_drain_completed,
            "daemon_drained": daemon_drained,
            "capture_types": list(capture_types),
        }
    )
    return ProductObservationRun(
        native_records=records,
        final_drain_completed=final_drain_completed,
        collector_shutdown_verified=collector_shutdown_verified,
        reconciliation_completed=independently_verified,
        authority_conflict=False,
        partial_success=not independently_verified,
        receipt=receipt,
        telemetry_spans=collector.spans,
        evidence_failure_reasons=tuple(evidence_failure_reasons),
    )


def _observation_telemetry_context(contract: ObservationContract) -> TelemetryContext:
    """Bind composition-emitted sentinel spans to opaque path and run identities."""

    digest = sha256(
        f"{contract.identity.conformance_sha256}:{contract.path.value}".encode("ascii")
    ).hexdigest()
    return TelemetryContext(
        experiment_id=f"exp_{digest[:32]}",
        protocol_id=f"protocol_{digest[:32]}",
        run_id=f"run_{digest[:32]}",
        attempt_id=f"attempt_{digest[:32]}",
        scenario_id=f"scenario_{digest[:32]}",
        stratum_id="product",
        history_mode="controlled",
        identity=identity_for_span_class(SpanClass.DAEMON_DISPATCH.value),
        evidence_class="observation_sentinel",
        exposure_state="unexposed",
        environment_fingerprint_sha256=digest,
    )


def receipt_sha256(run: ProductObservationRun) -> str:
    """Return the retained native receipt commitment used by the qualification manifest."""

    return sha256(run.receipt).hexdigest()
