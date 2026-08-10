"""Isolated direct ``MemoryEngine`` adapter using only its public async API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import Protocol

from memrelay_eval.canonical import attach_digest, canonical_bytes, canonical_digest
from memrelay_eval.domain.engine import (
    DirectEngineExecutionMode,
    DirectEngineIsolation,
    EngineExternalRecord,
    FrameworkConfiguration,
    LiveEngineEnvelope,
    RenderingContract,
    StratumAuthority,
    authority_document,
)
from memrelay_eval.domain.entities import (
    ArtifactLink,
    ArtifactRef,
    ExposureDecision,
    ExposureObservation,
    ExposureRecord,
    TelemetryObservation,
)
from memrelay_eval.domain.errors import DirectEngineBoundaryError
from memrelay_eval.domain.ids import AssignmentId, AttemptId, RunId
from memrelay_eval.domain.ports import ArtifactStorePort, LedgerPort, TelemetryPort
from memrelay_eval.domain.states import (
    EvaluationStratum,
    ExposureClassification,
    ExposurePhase,
)


class PublicMemoryEngine(Protocol):
    async def note(
        self,
        content: str,
        namespace: str,
        repo: str | None = None,
        source: str | None = None,
    ) -> str: ...

    async def search(
        self,
        query: str,
        namespace: str,
        prefer_repo: str | None = None,
        *,
        prefer_agent: str | None = None,
    ) -> dict[str, object]: ...

    async def detail(self, node_uuid: str, namespace: str) -> dict[str, object]: ...
    async def health(self) -> dict[str, object]: ...
    async def close(self) -> None: ...


class MemoryEngineType(Protocol):
    @classmethod
    async def from_config(cls, cfg: object) -> PublicMemoryEngine: ...


class LiveUsageMeter(Protocol):
    """Reserves prospective usage before a live call and reports consumption."""

    def reserve(self, method: str, envelope: LiveEngineEnvelope) -> None: ...
    def record(self, method: str) -> None: ...
    def snapshot(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class UnpaidEngineRuntime:
    """Explicit fake-only engine capability for deterministic unpaid execution."""

    engine_type: MemoryEngineType
    config_builder: Callable[[DirectEngineAttempt], object]
    provenance: str = "unpaid_conformance"
    eligible_for_paid_or_study: bool = False

    def __post_init__(self) -> None:
        if (
            self.provenance != "unpaid_conformance"
            or self.eligible_for_paid_or_study
            or getattr(self.engine_type, "provenance", None) != "unpaid_conformance"
            or getattr(self.engine_type, "eligible_for_paid_or_study", None) is not False
            or self.config_builder is _build_memrelay_config
        ):
            raise DirectEngineBoundaryError("unpaid_engine_capability_invalid")


@dataclass(frozen=True, slots=True)
class DirectEngineAttempt:
    attempt_id: AttemptId
    run_id: RunId
    assignment_id: AssignmentId
    authority: StratumAuthority
    isolation: DirectEngineIsolation
    framework: FrameworkConfiguration
    rendering: RenderingContract
    namespace: str
    execution_mode: DirectEngineExecutionMode = DirectEngineExecutionMode.UNPAID_FAKE
    live_envelope: LiveEngineEnvelope | None = None
    note_content: str | None = None
    note_repo: str | None = None
    note_source: str | None = None
    search_query: str | None = None
    search_prefer_repo: str | None = None
    search_prefer_agent: str | None = None
    detail_node_uuid: str | None = None

    def __post_init__(self) -> None:
        if self.authority.stratum is not EvaluationStratum.DIRECT_ENGINE:
            raise DirectEngineBoundaryError("engine_attempt_stratum_invalid")
        if self.authority.assignment_id != self.assignment_id:
            raise DirectEngineBoundaryError("engine_assignment_identity_mismatch")
        if self.authority.run_id != self.run_id:
            raise DirectEngineBoundaryError("engine_run_identity_mismatch")
        if self.authority.runtime_id != self.isolation.worker_id:
            raise DirectEngineBoundaryError("engine_runtime_identity_mismatch")
        if not self.namespace:
            raise DirectEngineBoundaryError("engine_namespace_invalid")
        if self.execution_mode is DirectEngineExecutionMode.LIVE_CONFORMANCE:
            if self.live_envelope is None:
                raise DirectEngineBoundaryError("live_engine_envelope_required")
        elif self.live_envelope is not None:
            raise DirectEngineBoundaryError("unpaid_engine_envelope_forbidden")


@dataclass(frozen=True, slots=True)
class DirectEngineEvidence:
    """Treatment evidence only; Inspect remains the execution terminal authority."""

    authority_artifact: ArtifactRef
    graph_artifact: ArtifactRef
    rendering_contract_artifact: ArtifactRef
    construction_artifact: ArtifactRef
    health: EngineExternalRecord | None
    note_result: str | None
    search: EngineExternalRecord | None
    detail: EngineExternalRecord | None
    rendered_search: str | None
    close_artifact: ArtifactRef
    artifacts: tuple[ArtifactRef, ...]


class DirectEngineAdapter:
    """Runs one isolated treatment and retains partial evidence on every path."""

    def __init__(
        self,
        store: ArtifactStorePort,
        ledger: LedgerPort,
        telemetry: TelemetryPort,
        graph_registry: object,
        *,
        unpaid_runtime: UnpaidEngineRuntime | None = None,
        live_usage_meter: LiveUsageMeter | None = None,
    ) -> None:
        self._store = store
        self._ledger = ledger
        self._telemetry = telemetry
        self._graphs = graph_registry
        self._unpaid_runtime = unpaid_runtime
        self._live_usage_meter = live_usage_meter

    async def execute(self, attempt: DirectEngineAttempt) -> DirectEngineEvidence:
        self._preflight_execution_mode(attempt)
        claim = getattr(self._graphs, "claim", None)
        if claim is None:
            raise DirectEngineBoundaryError("engine_graph_claim_port_invalid")
        claim(attempt.isolation.graph_path_digest, attempt.attempt_id)
        deadline = (
            time.monotonic() + attempt.live_envelope.maximum_wall_seconds
            if attempt.live_envelope is not None
            else None
        )
        artifacts: list[ArtifactRef] = []
        authority_artifact = self._record(
            attempt, "stratum_authority", authority_document(attempt.authority), artifacts
        )
        graph_artifact = self._record(
            attempt,
            "engine_graph",
            {
                "artifact_type": "engine_graph_identity",
                "schema_version": "1.0.0",
                "stratum": EvaluationStratum.DIRECT_ENGINE.value,
                "worker_id": str(attempt.isolation.worker_id),
                "graph_path_sha256": attempt.isolation.graph_path_digest,
                "product_graph_count": len(attempt.isolation.product_graph_paths),
            },
            artifacts,
        )
        rendering_artifact = self._record(
            attempt,
            "engine_rendering_contract",
            {
                "artifact_type": "engine_rendering_contract",
                "stratum": EvaluationStratum.DIRECT_ENGINE.value,
                "contract": attempt.rendering.to_document(),
                "contract_sha256": attempt.rendering.digest,
            },
            artifacts,
        )
        self._record(
            attempt,
            "engine_cost_identity",
            {
                "artifact_type": "engine_cost_identity",
                "schema_version": "1.0.0",
                "stratum": EvaluationStratum.DIRECT_ENGINE.value,
                "cost_entry_id": str(attempt.authority.cost_entry_id),
                "credential_domain": "openai_api",
                "execution_mode": attempt.execution_mode.value,
                "planned_envelope": (
                    attempt.live_envelope.to_document()
                    if attempt.live_envelope is not None
                    else None
                ),
                "request_quantity": "unavailable",
                "token_quantity": "unavailable",
                "usd_amount": "unavailable",
                "measurement": "unpaid_conformance",
            },
            artifacts,
        )

        engine: PublicMemoryEngine | None = None
        construction_artifact: ArtifactRef | None = None
        close_artifact: ArtifactRef | None = None
        health: EngineExternalRecord | None = None
        note_result: str | None = None
        search: EngineExternalRecord | None = None
        detail: EngineExternalRecord | None = None
        rendered_search: str | None = None
        failure: BaseException | None = None
        try:
            engine_class, config = self._execution_dependencies(attempt)
            engine_value, construction_artifact = await self._invoke(
                attempt,
                "construction",
                lambda: engine_class.from_config(config),
                deadline,
                artifacts,
            )
            engine = engine_value
            self._record_exposure(attempt, construction_artifact)
            health_external, _ = await self._invoke(
                attempt, "health", engine.health, deadline, artifacts
            )
            health = EngineExternalRecord.from_external(
                "health", _conceal_health(health_external, attempt.isolation)
            )
            self._record(attempt, "engine_health", health.to_document(), artifacts)
            if attempt.note_content is not None:
                note_value, _ = await self._invoke(
                    attempt,
                    "note",
                    lambda: engine.note(
                        attempt.note_content,
                        attempt.namespace,
                        attempt.note_repo,
                        attempt.note_source,
                    ),
                    deadline,
                    artifacts,
                )
                note_result = note_value
                if not isinstance(note_result, str):
                    raise DirectEngineBoundaryError("engine_note_result_invalid")
            if attempt.search_query is not None:
                search_external, _ = await self._invoke(
                    attempt,
                    "search",
                    lambda: engine.search(
                        attempt.search_query,
                        attempt.namespace,
                        attempt.search_prefer_repo,
                        prefer_agent=attempt.search_prefer_agent,
                    ),
                    deadline,
                    artifacts,
                )
                search = EngineExternalRecord.from_external(
                    "search",
                    search_external,
                )
                self._record(attempt, "engine_search", search.to_document(), artifacts)
                rendered_search = _render_search(search, attempt.rendering)
                self._record(
                    attempt,
                    "engine_rendered_search",
                    {
                        "artifact_type": "engine_rendered_search",
                        "rendering_contract_sha256": attempt.rendering.digest,
                        "text": rendered_search,
                    },
                    artifacts,
                )
            if attempt.detail_node_uuid is not None:
                detail_external, _ = await self._invoke(
                    attempt,
                    "detail",
                    lambda: engine.detail(attempt.detail_node_uuid, attempt.namespace),
                    deadline,
                    artifacts,
                )
                detail = EngineExternalRecord.from_external(
                    "detail",
                    detail_external,
                )
                self._record(attempt, "engine_detail", detail.to_document(), artifacts)
        except asyncio.CancelledError as error:
            failure = error
        except BaseException as error:
            failure = error
            if construction_artifact is None:
                construction_artifact = artifacts[-1]
                self._record_exposure(attempt, construction_artifact)
        finally:
            if engine is not None:
                try:
                    close_artifact = await self._close_engine(attempt, engine, artifacts)
                except asyncio.CancelledError as close_cancel:
                    if failure is None:
                        failure = close_cancel
                except BaseException as close_error:
                    close_artifact = artifacts[-1]
                    if failure is None:
                        failure = close_error
            else:
                close_artifact = self._event(attempt, "close", "not_constructed", artifacts)

        try:
            self._record_consumed_usage(attempt, artifacts)
        except BaseException as usage_error:
            if failure is None:
                failure = usage_error
        if failure is not None:
            if isinstance(failure, asyncio.CancelledError):
                raise failure
            if isinstance(failure, DirectEngineBoundaryError):
                raise DirectEngineBoundaryError(failure.code, tuple(artifacts)) from failure
            raise DirectEngineBoundaryError(
                "direct_engine_execution_failed", tuple(artifacts)
            ) from failure
        assert construction_artifact is not None
        assert close_artifact is not None
        return DirectEngineEvidence(
            authority_artifact,
            graph_artifact,
            rendering_artifact,
            construction_artifact,
            health,
            note_result,
            search,
            detail,
            rendered_search,
            close_artifact,
            tuple(artifacts),
        )

    def _preflight_execution_mode(self, attempt: DirectEngineAttempt) -> None:
        if attempt.execution_mode is DirectEngineExecutionMode.UNPAID_FAKE:
            if not isinstance(self._unpaid_runtime, UnpaidEngineRuntime):
                raise DirectEngineBoundaryError("unpaid_engine_capability_required")
            if (
                self._unpaid_runtime.provenance != "unpaid_conformance"
                or self._unpaid_runtime.eligible_for_paid_or_study
            ):
                raise DirectEngineBoundaryError("unpaid_engine_capability_invalid")
            if self._live_usage_meter is not None:
                raise DirectEngineBoundaryError("unpaid_engine_usage_meter_forbidden")
            return
        if self._unpaid_runtime is not None:
            raise DirectEngineBoundaryError("live_engine_fake_capability_forbidden")
        if self._live_usage_meter is None:
            raise DirectEngineBoundaryError("live_engine_usage_meter_required")

    def _execution_dependencies(
        self, attempt: DirectEngineAttempt
    ) -> tuple[MemoryEngineType, object]:
        if attempt.execution_mode is DirectEngineExecutionMode.UNPAID_FAKE:
            assert self._unpaid_runtime is not None
            return (
                self._unpaid_runtime.engine_type,
                self._unpaid_runtime.config_builder(attempt),
            )
        return _load_memory_engine(), _build_memrelay_config(attempt)

    async def _invoke(
        self,
        attempt: DirectEngineAttempt,
        method: str,
        operation: Callable[[], Awaitable[object]],
        deadline: float | None,
        artifacts: list[ArtifactRef],
    ) -> tuple[object, ArtifactRef]:
        meter = self._live_usage_meter
        is_cleanup = method == "close"
        started = time.monotonic()
        try:
            if meter is not None and not is_cleanup:
                assert attempt.live_envelope is not None
                meter.reserve(method, attempt.live_envelope)
            awaitable = operation()
            if deadline is None:
                result = await awaitable
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    close_awaitable = getattr(awaitable, "close", None)
                    if close_awaitable is not None:
                        close_awaitable()
                    raise TimeoutError
                result = await asyncio.wait_for(awaitable, timeout=remaining)
        except asyncio.CancelledError:
            artifact = self._boundary_event(
                attempt, method, "cancelled", "engine_call_cancelled", started, artifacts
            )
            raise
        except TimeoutError as error:
            self._boundary_event(
                attempt,
                method,
                "timed_out",
                "engine_call_timed_out",
                started,
                artifacts,
                error,
            )
            raise DirectEngineBoundaryError("engine_call_timed_out", tuple(artifacts)) from error
        except BaseException as error:
            code = (
                error.code if isinstance(error, DirectEngineBoundaryError) else "engine_call_failed"
            )
            self._boundary_event(attempt, method, "failed", code, started, artifacts, error)
            raise
        if meter is not None and not is_cleanup:
            meter.record(method)
            self._require_usage_within_envelope(attempt, meter.snapshot())
        artifact = self._boundary_event(
            attempt,
            method,
            "succeeded",
            "engine_call_succeeded",
            started,
            artifacts,
            result=result,
        )
        return result, artifact

    async def _close_engine(
        self,
        attempt: DirectEngineAttempt,
        engine: PublicMemoryEngine,
        artifacts: list[ArtifactRef],
    ) -> ArtifactRef:
        close_task = asyncio.create_task(
            self._invoke(
                attempt,
                "close",
                engine.close,
                time.monotonic() + 5.0,
                artifacts,
            )
        )
        try:
            _, artifact = await asyncio.wait_for(asyncio.shield(close_task), timeout=5.1)
            return artifact
        except asyncio.CancelledError:
            try:
                _, artifact = await asyncio.wait_for(asyncio.shield(close_task), timeout=5.1)
                return artifact
            finally:
                raise

    def _boundary_event(
        self,
        attempt: DirectEngineAttempt,
        method: str,
        outcome: str,
        code: str,
        started: float,
        artifacts: list[ArtifactRef],
        error: BaseException | None = None,
        result: object | None = None,
    ) -> ArtifactRef:
        exception_type = (
            f"{type(error).__module__}.{type(error).__qualname__}" if error is not None else None
        )
        partial_refs = [artifact.sha256 for artifact in artifacts]
        usage = (
            dict(self._live_usage_meter.snapshot())
            if self._live_usage_meter is not None
            else {"calls": 0, "tokens": 0, "usd": 0, "wall_seconds": 0}
        )
        success_details: dict[str, object] = {}
        if outcome == "succeeded" and method == "construction":
            success_details["framework_configuration_sha256"] = attempt.framework.digest
        if outcome == "succeeded" and method == "note" and isinstance(result, str):
            success_details["result"] = result
        return self._event(
            attempt,
            method,
            outcome,
            artifacts,
            {
                "failure_classification": code if outcome != "succeeded" else None,
                "failure_code": code if outcome != "succeeded" else None,
                "exception_type": exception_type,
                "exception_type_sha256": (
                    sha256(exception_type.encode("utf-8")).hexdigest()
                    if exception_type is not None
                    else None
                ),
                "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
                "partial_evidence_sha256": partial_refs,
                "usage": usage,
                **success_details,
            },
        )

    def _record_consumed_usage(
        self, attempt: DirectEngineAttempt, artifacts: list[ArtifactRef]
    ) -> None:
        if self._live_usage_meter is None:
            consumed: Mapping[str, object] = {
                "calls": 0,
                "tokens": 0,
                "usd": 0,
                "wall_seconds": 0,
            }
        else:
            consumed = self._live_usage_meter.snapshot()
        self._record(
            attempt,
            "engine_consumed_usage",
            {
                "artifact_type": "engine_consumed_usage",
                "schema_version": "1.0.0",
                "stratum": EvaluationStratum.DIRECT_ENGINE.value,
                "execution_mode": attempt.execution_mode.value,
                "consumed": dict(consumed),
            },
            artifacts,
        )
        self._require_usage_within_envelope(attempt, consumed)

    @staticmethod
    def _require_usage_within_envelope(
        attempt: DirectEngineAttempt, consumed: Mapping[str, object]
    ) -> None:
        envelope = attempt.live_envelope
        if envelope is None:
            return
        limits = {
            "calls": envelope.maximum_calls,
            "tokens": envelope.maximum_tokens,
            "usd": envelope.maximum_usd,
            "wall_seconds": envelope.maximum_wall_seconds,
        }
        for key, limit in limits.items():
            value = consumed.get(key)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
                or value > limit
            ):
                raise DirectEngineBoundaryError("live_engine_usage_limit_exceeded")

    def _record_exposure(self, attempt: DirectEngineAttempt, artifact: ArtifactRef) -> None:
        now = datetime.now(UTC)
        observation = ExposureObservation(
            ExposurePhase.MEMORY_PROVISION,
            True,
            now,
            time.monotonic(),
            (artifact,),
        )
        self._ledger.append_exposure_record(
            ExposureRecord(
                attempt.attempt_id,
                attempt.assignment_id,
                ExposureDecision(
                    ExposureClassification.EXPOSED,
                    (artifact,),
                    "direct_engine_treatment_access",
                    observation.monotonic_seconds,
                    (observation,),
                ),
            )
        )

    def _event(
        self,
        attempt: DirectEngineAttempt,
        method: str,
        outcome: str,
        artifacts: list[ArtifactRef],
        extra: Mapping[str, object] | None = None,
    ) -> ArtifactRef:
        return self._record(
            attempt,
            f"engine_{method}",
            {
                "artifact_type": "direct_engine_api_event",
                "schema_version": "1.0.0",
                "stratum": EvaluationStratum.DIRECT_ENGINE.value,
                "method": method,
                "outcome": outcome,
                **dict(extra or {}),
            },
            artifacts,
        )

    def _record(
        self,
        attempt: DirectEngineAttempt,
        purpose: str,
        document: Mapping[str, object],
        artifacts: list[ArtifactRef],
    ) -> ArtifactRef:
        artifact = self._store.put_bytes(
            canonical_bytes(attach_digest(document)),
            media_type="application/json",
            classification=purpose,
        )
        artifacts.append(artifact)
        self._ledger.append_artifact_link(
            ArtifactLink(
                artifact,
                purpose,
                run_id=attempt.run_id,
                attempt_id=attempt.attempt_id,
            )
        )
        self._telemetry.emit(
            TelemetryObservation(
                f"direct_engine_{purpose}",
                datetime.now(UTC),
                {
                    "attempt_id": str(attempt.attempt_id),
                    "stratum": EvaluationStratum.DIRECT_ENGINE.value,
                    "artifact_sha256": artifact.sha256,
                },
            )
        )
        return artifact


def _load_memory_engine() -> MemoryEngineType:
    module = import_module("memrelay.engine.graphiti")
    return module.MemoryEngine


def _build_memrelay_config(attempt: DirectEngineAttempt) -> object:
    config = import_module("memrelay.config")
    framework = attempt.framework
    return config.Config(
        home=str(attempt.isolation.home_path),
        graph=config.GraphConfig(backend="ladybug", path=str(attempt.isolation.graph_path)),
        llm=config.LLMConfig(
            strategy=framework.llm_strategy,
            host="copilot",
            provider=framework.llm_provider,
            api_key_env=framework.llm_api_key_env,
            model=framework.llm_model,
        ),
        embeddings=config.EmbeddingsConfig(
            provider=framework.embeddings_provider,
            model=framework.embeddings_model,
        ),
    )


def _render_search(record: EngineExternalRecord, contract: RenderingContract) -> str:
    nodes = record.payload.get("nodes")
    if not isinstance(nodes, tuple) or not nodes:
        return contract.empty_search_text
    lines: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            raise DirectEngineBoundaryError("engine_search_record_invalid")
        name = node.get("name")
        summary = node.get("summary")
        if not isinstance(name, str) or not isinstance(summary, (str, type(None))):
            raise DirectEngineBoundaryError("engine_search_record_invalid")
        lines.append(contract.search_template.format(name=name, summary=summary or ""))
    return "\n".join(lines)


def _conceal_health(value: object, isolation: DirectEngineIsolation) -> dict[str, object]:
    if not isinstance(value, dict):
        raise DirectEngineBoundaryError("engine_external_result_not_plain_dictionary")
    health = dict(value)
    graph_path = health.pop("graph_path", None)
    if graph_path is not None:
        if not isinstance(graph_path, str):
            raise DirectEngineBoundaryError("engine_health_graph_path_invalid")
        actual = canonical_digest({"graph_path": str(Path(graph_path).resolve())})
        if actual != isolation.graph_path_digest:
            raise DirectEngineBoundaryError("engine_health_graph_mismatch")
        health["graph_path_sha256"] = isolation.graph_path_digest
    return health
