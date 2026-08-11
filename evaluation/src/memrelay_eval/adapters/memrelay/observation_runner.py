"""Execute the shipped observation compositions behind evaluator-owned seams."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
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
    manifest_records: tuple[SentinelBoundaryRecord, ...]
    reconciliation_records: tuple[SentinelBoundaryRecord, ...]


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

    def reorder_arrivals(self) -> None:
        """Inject a collector-side delivery rotation for deterministic fault coverage."""

        if len(self._spans) > 1:
            self._spans = [self._spans[-1], *self._spans[:-1]]

    def shutdown(self) -> bool:
        self._shutdown_verified = True
        return True


@dataclass(frozen=True, slots=True)
class _LiveTailDelivery:
    """Value-safe delivery facts read by the real live-tail source."""

    identifiers: tuple[str, ...]
    observed_at: Mapping[str, datetime]
    restart_epochs: Mapping[str, int]
    failed: bool


@dataclass(frozen=True, slots=True)
class _SentinelArrival:
    """One value-safe retained arrival from a concrete observation seam."""

    identifier: str
    observed_at: datetime
    restart_epoch: int


class _RecordingTailSource:
    """Proxy a live source while retaining only sentinel IDs and source timestamps."""

    def __init__(self, source: Any, expected_ids: Sequence[str], *, restart_epoch: int) -> None:
        self._source = source
        self._expected_ids = tuple(expected_ids)
        self._restart_epoch = restart_epoch
        self._entered: Any | None = None
        self._identifiers: list[str] = []
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
                self._identifiers.append(identifier)
                self._observed_at.setdefault(identifier, timestamp)

    def delivery(self) -> _LiveTailDelivery:
        return _LiveTailDelivery(
            identifiers=tuple(self._identifiers),
            observed_at=dict(self._observed_at),
            restart_epochs=dict.fromkeys(self._observed_at, self._restart_epoch),
            failed=self.failed,
        )


class _RecordingSpool:
    """Keep pre-idempotency ingress facts while delegating persistence to the real spool."""

    def __init__(
        self,
        spool: Any,
        *,
        expected_ids: Sequence[str],
        source_times: Mapping[str, datetime],
        restart_epoch_for_session: Callable[[object], int],
        inject_post_idempotency_duplicate: bool,
    ) -> None:
        self._spool = spool
        self._expected_ids = tuple(expected_ids)
        self._source_times = source_times
        self._restart_epoch_for_session = restart_epoch_for_session
        self._inject_post_idempotency_duplicate = inject_post_idempotency_duplicate
        self._duplicate_injected = False
        self.pre_idempotency_records: list[dict[str, Any]] = []
        self.pre_idempotency_arrivals: list[_SentinelArrival] = []
        self._spool_arrivals_by_key: dict[str, tuple[_SentinelArrival, ...]] = {}

    def append(self, record: dict[str, Any]) -> None:
        self.pre_idempotency_records.append(dict(record))
        arrivals = self._arrivals_for(record)
        self.pre_idempotency_arrivals.extend(arrivals)
        key = record.get("idempotency_key")
        if isinstance(key, str):
            self._spool_arrivals_by_key.setdefault(key, arrivals)
        self._spool.append(record)
        if (
            self._inject_post_idempotency_duplicate
            and not self._duplicate_injected
            and arrivals
            and isinstance(key, str)
        ):
            duplicate = dict(record)
            duplicate_key = f"{key}:observation-post-idempotency-duplicate"
            duplicate["idempotency_key"] = duplicate_key
            self._spool_arrivals_by_key[duplicate_key] = arrivals
            self._spool.append(duplicate)
            self._duplicate_injected = True

    def spool_arrivals(
        self, stored_records: Sequence[Mapping[str, object]]
    ) -> tuple[_SentinelArrival, ...]:
        arrivals: list[_SentinelArrival] = []
        for record in stored_records:
            key = record.get("idempotency_key")
            if isinstance(key, str):
                arrivals.extend(self._spool_arrivals_by_key.get(key, ()))
        return tuple(arrivals)

    def _arrivals_for(self, record: Mapping[str, object]) -> tuple[_SentinelArrival, ...]:
        content = record.get("content")
        if not isinstance(content, str):
            return ()
        restart_epoch = self._restart_epoch_for_session(record.get("session_id"))
        recorded_at = _parse_observed_at(record.get("ts"))
        arrivals: list[_SentinelArrival] = []
        for identifier in self._expected_ids:
            if identifier in content:
                arrivals.append(
                    _SentinelArrival(
                        identifier=identifier,
                        observed_at=recorded_at or self._source_times[identifier],
                        restart_epoch=restart_epoch,
                    )
                )
        return tuple(arrivals)


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
    return b"".join(canonical_bytes(event) + b"\n" for event in events).decode("utf-8")


def _records(
    *,
    path: ObservationPath,
    boundary: ObservationBoundary,
    arrivals: Sequence[_SentinelArrival],
    expected_sequences: Mapping[str, int],
) -> tuple[SentinelBoundaryRecord, ...]:
    return tuple(
        SentinelBoundaryRecord(
            path=path,
            boundary=boundary,
            sentinel_id=arrival.identifier,
            sequence=expected_sequences[arrival.identifier],
            observed_at=arrival.observed_at,
            restart_epoch=arrival.restart_epoch,
        )
        for arrival in arrivals
        if arrival.identifier in expected_sequences
    )


def _reordered_arrivals(
    arrivals: Sequence[_SentinelArrival], *, inject: bool
) -> tuple[_SentinelArrival, ...]:
    """Model a seam-local rotation while keeping ordinary producer order untouched."""

    retained = tuple(arrivals)
    if inject and len(retained) > 1:
        return retained[-1:] + retained[:-1]
    return retained


def _arrivals_for_identifiers(
    identifiers: Sequence[str],
    *,
    source_times: Mapping[str, datetime],
    restart_epochs: Mapping[str, int],
) -> tuple[_SentinelArrival, ...]:
    return tuple(
        _SentinelArrival(
            identifier=identifier,
            observed_at=source_times[identifier],
            restart_epoch=restart_epochs.get(identifier, 0),
        )
        for identifier in identifiers
        if identifier in source_times
    )


def _first_epoch_by_identifier(
    arrivals: Sequence[_SentinelArrival],
) -> dict[str, int]:
    epochs: dict[str, int] = {}
    for arrival in arrivals:
        epochs.setdefault(arrival.identifier, arrival.restart_epoch)
    return epochs


def _last_epoch_by_identifier(
    arrivals: Sequence[_SentinelArrival],
) -> dict[str, int]:
    epochs: dict[str, int] = {}
    for arrival in arrivals:
        epochs[arrival.identifier] = max(arrival.restart_epoch, epochs.get(arrival.identifier, 0))
    return epochs


def _parse_fault_injections(
    fault_injections: Sequence[str],
) -> tuple[frozenset[ObservationBoundary], bool]:
    """Restrict deterministic test faults to explicit observation seams."""

    reordered: set[ObservationBoundary] = set()
    duplicate_spool = False
    for injection in fault_injections:
        if injection == "duplicate:spool":
            duplicate_spool = True
            continue
        action, separator, boundary_name = injection.partition(":")
        if action != "reorder" or not separator:
            raise ObservationQualificationError("observation_fault_injection_invalid")
        try:
            reordered.add(ObservationBoundary(boundary_name))
        except ValueError as error:
            raise ObservationQualificationError("observation_fault_injection_invalid") from error
    return frozenset(reordered), duplicate_spool


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


async def _exercise_capture_composition(
    *,
    contract: ObservationContract,
    config: Any,
    workspace: Path,
    tail_source_factory: Callable[[Any], Any] | None,
    inject_post_idempotency_duplicate: bool,
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[_SentinelArrival, ...],
    tuple[_SentinelArrival, ...],
    tuple[_SentinelArrival, ...],
    tuple[_SentinelArrival, ...],
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
    identifier_by_session = {
        ref.session_id: sentinel.identifier
        for ref, sentinel in zip(refs, contract.expected_sentinels, strict=True)
    }
    lifecycle_epochs = {ref.session_id: -1 for ref in refs}
    discovery_cycles: list[tuple[str, ...]] = []
    capture_arrivals: list[_SentinelArrival] = []
    tail_probes: list[_RecordingTailSource] = []
    tail_captures: list[tuple[_RecordingTailSource, object]] = []
    tail_task_failed = False

    async def replay_wait(_: float, stop: asyncio.Event) -> None:
        nonlocal replay_waits
        replay_waits += 1
        await stop.wait()

    with Spool(spool_path) as persisted_spool:
        spool = _RecordingSpool(
            persisted_spool,
            expected_ids=contract.expected_identifiers,
            source_times=source_times,
            restart_epoch_for_session=lambda session_id: lifecycle_epochs.get(session_id, 0),
            inject_post_idempotency_duplicate=inject_post_idempotency_duplicate,
        )

        def capture_factory(ref: SessionRef) -> object:
            lifecycle_epochs[ref.session_id] += 1
            identifier = identifier_by_session[ref.session_id]
            capture_arrivals.append(
                _SentinelArrival(
                    identifier=identifier,
                    observed_at=source_times[identifier],
                    restart_epoch=lifecycle_epochs[ref.session_id],
                )
            )
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
                probe = _RecordingTailSource(
                    source,
                    contract.expected_identifiers,
                    restart_epoch=lifecycle_epochs[ref.session_id],
                )
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
                    lambda: (
                        set(contract.expected_identifiers).issubset(
                            {
                                identifier
                                for probe in probes
                                for identifier in probe.delivery().identifiers
                            }
                        )
                        or all(probe.failed or probe.exhausted for probe in probes)
                    ),
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

        def discover() -> list[SessionRef]:
            discovered = list(active)
            discovery_cycles.append(
                tuple(identifier_by_session[ref.session_id] for ref in discovered)
            )
            return discovered

        poller = SessionDiscoveryPoller(
            discover=discover,
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
        spool_arrivals = spool.spool_arrivals(stored_records)
        final_drain_completed = (
            final_stats["sessions_observed"] == 2 * len(refs)
            and final_stats["active_sessions"] == 0
            and {arrival.identifier for arrival in spool_arrivals}
            == set(contract.expected_identifiers)
            and all(getattr(capture, "_task", None) is None for capture in captures.values())
        )
        if contract.path is ObservationPath.FILE_WATCH:
            final_drain_completed = final_drain_completed and all(
                getattr(capture, "_tail_task", None) is None
                and isinstance(getattr(capture, "_replay", None), RunObserveCapture)
                for capture in captures.values()
            )
        tail_identifiers: list[str] = []
        tail_observed_at: dict[str, datetime] = {}
        tail_restart_epochs: dict[str, int] = {}
        tail_failed = tail_task_failed
        latest_tail_probes = tail_probes[-len(refs) :]
        for probe in latest_tail_probes:
            delivery = probe.delivery()
            tail_identifiers.extend(delivery.identifiers)
            for identifier, observed_at in delivery.observed_at.items():
                tail_observed_at.setdefault(identifier, observed_at)
            for identifier, restart_epoch in delivery.restart_epochs.items():
                tail_restart_epochs.setdefault(identifier, restart_epoch)
            tail_failed = tail_failed or delivery.failed
        latest_discovery = next((cycle for cycle in reversed(discovery_cycles) if cycle), ())
        discovery_arrivals = _arrivals_for_identifiers(
            latest_discovery,
            source_times=source_times,
            restart_epochs={
                identifier_by_session[ref.session_id]: lifecycle_epochs[ref.session_id]
                for ref in refs
            },
        )
        latest_capture_arrivals = tuple(capture_arrivals[-len(refs) :])
    return (
        stored_records,
        discovery_arrivals,
        latest_capture_arrivals,
        tuple(spool.pre_idempotency_arrivals),
        spool_arrivals,
        final_stats["sessions_observed"],
        final_drain_completed,
        tuple(capture_types),
        (
            str(stored_records[0]["namespace"]) if stored_records else "",
            stored_records[0].get("repo") if stored_records else None,
        ),
        _LiveTailDelivery(
            identifiers=tuple(tail_identifiers),
            observed_at=tail_observed_at,
            restart_epochs=tail_restart_epochs,
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
    restart_epochs: Mapping[str, int],
    telemetry: TelemetryAttemptEmitter,
    required_note_count: int,
    reordered_boundaries: frozenset[ObservationBoundary],
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Drain through the actual daemon and query it through the actual MCP tool surface."""

    from memrelay.daemon.runtime import DaemonRuntime
    from memrelay.daemon.transport import resolve_endpoint
    from memrelay.mcp.client import DaemonClient
    from memrelay.mcp.server import build_mcp_server

    with _bounded_daemon_home(workspace) as daemon_home:
        endpoint = resolve_endpoint(daemon_home)
        backend = _ObservationBackend()
        runtime = DaemonRuntime(config, endpoint, backend=backend)
        await runtime.start()
        serve_task = asyncio.create_task(runtime.serve())
        client = DaemonClient(endpoint, timeout=5.0)
        try:
            await _wait_until(
                lambda: len(backend.notes) >= required_note_count,
            )
            daemon_ids = tuple(
                identifier
                for content, _, _ in backend.notes
                for identifier in expected_ids
                if identifier in content
            )
            if ObservationBoundary.DAEMON in reordered_boundaries:
                daemon_ids = _reordered_arrivals(
                    _arrivals_for_identifiers(
                        daemon_ids,
                        source_times=observed_at,
                        restart_epochs=restart_epochs,
                    ),
                    inject=True,
                )
                daemon_ids = tuple(arrival.identifier for arrival in daemon_ids)
            mcp_query_ids = daemon_ids
            if ObservationBoundary.MCP_GRAPH in reordered_boundaries:
                mcp_query_ids = tuple(
                    arrival.identifier
                    for arrival in _reordered_arrivals(
                        _arrivals_for_identifiers(
                            mcp_query_ids,
                            source_times=observed_at,
                            restart_epochs=restart_epochs,
                        ),
                        inject=True,
                    )
                )
            mcp_ids: list[str] = []
            for identifier in mcp_query_ids:
                mcp = build_mcp_server(client, context_resolver=lambda: (namespace, repo))
                result = await mcp.call_tool("memory_recall", {"query": identifier})
                blocks = result[0] if isinstance(result, tuple) else result
                text = blocks[0].text if blocks else ""
                if identifier in text:
                    mcp_ids.append(identifier)
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
                                "restart_epoch": restart_epochs.get(identifier, 0),
                            },
                        )
            health = await client.health()
            return daemon_ids, tuple(mcp_ids), health.get("spool_pending") == 0
        finally:
            runtime.request_shutdown()
            await asyncio.wait_for(serve_task, timeout=5.0)


@contextmanager
def _bounded_daemon_home(workspace: Path) -> Iterator[Path]:
    """Use an OS-shortened endpoint home for POSIX Unix-domain sockets."""

    if sys.platform == "win32":
        yield workspace / "daemon"
        return
    with TemporaryDirectory(prefix="memrelay-observation-") as temporary:
        daemon_home = Path(temporary)
        if len(os.fsencode(daemon_home / "daemon.sock")) >= 104:
            raise ObservationQualificationError("observation_daemon_socket_path_too_long")
        yield daemon_home


def run_actual_observation_composition(
    *,
    contract: ObservationContract,
    config: Any,
    workspace: Path,
    tail_source_factory: Callable[[Any], Any] | None = None,
    telemetry_collector_factory: Callable[[], ObservationTelemetryCollector] | None = None,
    fault_injections: Sequence[str] = (),
) -> ProductObservationRun:
    """Inject sentinels and collect product records plus composition-emitted telemetry."""

    reordered_boundaries, inject_post_idempotency_duplicate = _parse_fault_injections(
        fault_injections
    )
    workspace.mkdir(parents=True, exist_ok=False)
    (
        stored_records,
        discovery_arrivals,
        capture_arrivals,
        pre_idempotency_arrivals,
        raw_spool_arrivals,
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
            inject_post_idempotency_duplicate=inject_post_idempotency_duplicate,
        )
    )
    expected_ids = contract.expected_identifiers
    sentinels = contract.expected_sentinels
    expected_sequences = {sentinel.identifier: sentinel.sequence for sentinel in sentinels}
    source_times = _source_event_times(contract)
    first_spool_epochs = _first_epoch_by_identifier(raw_spool_arrivals)
    recovery_epochs = _last_epoch_by_identifier(
        discovery_arrivals + capture_arrivals + pre_idempotency_arrivals
    )
    observed_at = dict(source_times)
    for arrival in raw_spool_arrivals + pre_idempotency_arrivals:
        observed_at[arrival.identifier] = arrival.observed_at
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
                restart_epochs=first_spool_epochs,
                telemetry=telemetry,
                required_note_count=len(raw_spool_arrivals),
                reordered_boundaries=reordered_boundaries,
            )
        )
    finally:
        if ObservationBoundary.TELEMETRY in reordered_boundaries:
            collector.reorder_arrivals()
        collector_shutdown_verified = collector.shutdown()
    discovery_arrivals = _reordered_arrivals(
        discovery_arrivals,
        inject=ObservationBoundary.DISCOVERY in reordered_boundaries,
    )
    capture_arrivals = _reordered_arrivals(
        capture_arrivals,
        inject=ObservationBoundary.CAPTURE in reordered_boundaries,
    )
    pre_idempotency_arrivals = _reordered_arrivals(
        pre_idempotency_arrivals,
        inject=ObservationBoundary.PRE_IDEMPOTENCY in reordered_boundaries,
    )
    spool_arrivals = _reordered_arrivals(
        raw_spool_arrivals,
        inject=ObservationBoundary.SPOOL in reordered_boundaries,
    )
    live_tail_arrivals = _reordered_arrivals(
        _arrivals_for_identifiers(
            live_tail.identifiers,
            source_times=live_tail.observed_at,
            restart_epochs=live_tail.restart_epochs,
        ),
        inject=ObservationBoundary.LIVE_TAIL in reordered_boundaries,
    )
    daemon_arrivals = _arrivals_for_identifiers(
        daemon_ids,
        source_times=observed_at,
        restart_epochs=first_spool_epochs,
    )
    mcp_arrivals = _arrivals_for_identifiers(
        mcp_ids,
        source_times=observed_at,
        restart_epochs=first_spool_epochs,
    )
    terminal_arrivals = _reordered_arrivals(
        _arrivals_for_identifiers(
            tuple(arrival.identifier for arrival in raw_spool_arrivals)
            if final_drain_completed
            else (),
            source_times=source_times,
            restart_epochs=recovery_epochs,
        ),
        inject=ObservationBoundary.TERMINAL_FLUSH in reordered_boundaries,
    )
    manifest_records = _records(
        path=contract.path,
        boundary=ObservationBoundary.MANIFEST,
        arrivals=_reordered_arrivals(
            _arrivals_for_identifiers(
                tuple(arrival.identifier for arrival in raw_spool_arrivals),
                source_times=source_times,
                restart_epochs=recovery_epochs,
            ),
            inject=ObservationBoundary.MANIFEST in reordered_boundaries,
        ),
        expected_sequences=expected_sequences,
    )
    reconciliation_records = _records(
        path=contract.path,
        boundary=ObservationBoundary.RECONCILIATION,
        arrivals=_reordered_arrivals(
            _arrivals_for_identifiers(
                tuple(arrival.identifier for arrival in raw_spool_arrivals),
                source_times=source_times,
                restart_epochs=recovery_epochs,
            ),
            inject=ObservationBoundary.RECONCILIATION in reordered_boundaries,
        ),
        expected_sequences=expected_sequences,
    )
    capture_composition_verified = len(capture_types) == 2 * len(expected_ids) and all(
        item
        == ("RunObserveCapture" if contract.path is ObservationPath.REPLAY else "LiveTailCapture")
        for item in capture_types
    )
    live_tail_delivery_verified = (
        set(live_tail.identifiers) == set(expected_ids) and not live_tail.failed
    )
    records = (
        _records(
            path=contract.path,
            boundary=ObservationBoundary.DISCOVERY,
            arrivals=discovery_arrivals,
            expected_sequences=expected_sequences,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.LIVE_TAIL,
            arrivals=live_tail_arrivals,
            expected_sequences=expected_sequences,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.CAPTURE,
            arrivals=capture_arrivals,
            expected_sequences=expected_sequences,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.PRE_IDEMPOTENCY,
            arrivals=pre_idempotency_arrivals,
            expected_sequences=expected_sequences,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.SPOOL,
            arrivals=spool_arrivals,
            expected_sequences=expected_sequences,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.DAEMON,
            arrivals=daemon_arrivals,
            expected_sequences=expected_sequences,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.MCP_GRAPH,
            arrivals=mcp_arrivals,
            expected_sequences=expected_sequences,
        )
        + _records(
            path=contract.path,
            boundary=ObservationBoundary.TERMINAL_FLUSH,
            arrivals=terminal_arrivals,
            expected_sequences=expected_sequences,
        )
    )
    expected_membership = set(expected_ids)
    independently_verified = (
        sessions_observed == 2 * len(expected_ids)
        and {arrival.identifier for arrival in discovery_arrivals} == expected_membership
        and capture_composition_verified
        and {arrival.identifier for arrival in capture_arrivals} == expected_membership
        and {arrival.identifier for arrival in pre_idempotency_arrivals} == expected_membership
        and {arrival.identifier for arrival in raw_spool_arrivals} == expected_membership
        and set(daemon_ids) == expected_membership
        and set(mcp_ids) == expected_membership
        and final_drain_completed
        and daemon_drained
        and (contract.path is ObservationPath.REPLAY or live_tail_delivery_verified)
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
            "discovery_ids": [arrival.identifier for arrival in discovery_arrivals],
            "capture_ids": [arrival.identifier for arrival in capture_arrivals],
            "pre_idempotency_ids": [arrival.identifier for arrival in pre_idempotency_arrivals],
            "spool_ids": [arrival.identifier for arrival in spool_arrivals],
            "daemon_ids": list(daemon_ids),
            "mcp_ids": list(mcp_ids),
            "live_tail_ids": list(live_tail.identifiers),
            "terminal_flush_ids": [arrival.identifier for arrival in terminal_arrivals],
            "manifest_ids": [record.sentinel_id for record in manifest_records],
            "reconciliation_ids": [record.sentinel_id for record in reconciliation_records],
            "restart_epochs": {
                identifier: recovery_epochs.get(identifier, 0) for identifier in expected_ids
            },
            "fault_injections": list(fault_injections),
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
        manifest_records=manifest_records,
        reconciliation_records=reconciliation_records,
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
