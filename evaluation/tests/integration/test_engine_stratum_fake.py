from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.adapters.memrelay.engine import (
    DirectEngineAdapter,
    DirectEngineAttempt,
    UnpaidEngineRuntime,
)
from memrelay_eval.adapters.telemetry.semantics import SpanClass, TelemetryContext
from memrelay_eval.domain.engine import (
    DirectEngineExecutionMode,
    DirectEngineIsolation,
    FrameworkConfiguration,
    LiveEngineEnvelope,
    RenderingContract,
    StratumAuthority,
)
from memrelay_eval.domain.errors import DirectEngineBoundaryError
from memrelay_eval.domain.identity import framework_openai_identity
from memrelay_eval.domain.ids import (
    AnalysisId,
    AssignmentId,
    AttemptId,
    ClaimId,
    CostEntryId,
    EndpointId,
    ProtocolId,
    ReportId,
    RunId,
    RuntimeId,
)
from memrelay_eval.domain.states import EvaluationStratum, ExposureClassification
from memrelay_eval.orchestration.control import DirectEngineGraphClaimRegistry


class FaultingEngine:
    provenance = "unpaid_conformance"
    eligible_for_paid_or_study = False
    closed = False
    graph_path = ""
    fault_method = "search"
    blocker: asyncio.Event | None = None

    @classmethod
    async def from_config(cls, cfg: object) -> FaultingEngine:
        del cfg
        cls.closed = False
        return cls()

    async def health(self) -> dict[str, object]:
        if self.fault_method == "health":
            raise RuntimeError("synthetic secret health fault")
        return {"status": "ok", "graph_path": self.graph_path}

    async def note(
        self, content: str, namespace: str, repo: str | None = None, source: str | None = None
    ) -> str:
        del content, namespace, repo, source
        if self.fault_method == "note":
            raise TimeoutError("synthetic secret timeout")
        return "noted"

    async def search(
        self,
        query: str,
        namespace: str,
        prefer_repo: str | None = None,
        *,
        prefer_agent: str | None = None,
    ) -> dict[str, object]:
        del query, namespace, prefer_repo, prefer_agent
        if self.fault_method == "cancel":
            assert self.blocker is not None
            await self.blocker.wait()
        if self.fault_method == "search":
            raise RuntimeError("synthetic secret search fault")
        return {"nodes": [], "edges": [], "scores": []}

    async def detail(self, node_uuid: str, namespace: str) -> dict[str, object]:
        del node_uuid, namespace
        if self.fault_method == "detail":
            raise RuntimeError("synthetic secret detail fault")
        return {}

    async def close(self) -> None:
        type(self).closed = True
        if self.fault_method == "close":
            raise RuntimeError("synthetic secret close fault")


def _attempt(tmp_path: Path, graph_name: str = "graph.db") -> DirectEngineAttempt:
    assignment_id = AssignmentId.new()
    run_id = RunId.new()
    attempt_id = AttemptId.new()
    runtime_id = RuntimeId.new()
    authority = StratumAuthority(
        EvaluationStratum.DIRECT_ENGINE,
        ProtocolId.new(),
        assignment_id,
        run_id,
        runtime_id,
        EndpointId.new(),
        CostEntryId.new(),
        AnalysisId.new(),
        ReportId.new(),
        ClaimId.new(),
        "mechanism_upper_bound",
        "engine upper bound",
    )
    home = tmp_path / "engine"
    return DirectEngineAttempt(
        attempt_id,
        run_id,
        assignment_id,
        authority,
        DirectEngineIsolation(runtime_id, home, home / graph_name, (tmp_path / "daemon.db",)),
        FrameworkConfiguration(),
        RenderingContract("1.0.0", "{name}: {summary}", "No memories."),
        "namespace",
        note_content="fact",
        search_query="fact",
        telemetry_context=TelemetryContext(
            experiment_id="exp_" + "1" * 32,
            protocol_id=str(authority.protocol_id),
            run_id=str(run_id),
            attempt_id=str(attempt_id),
            scenario_id="scenario_" + "2" * 32,
            stratum_id="direct_engine",
            history_mode="controlled",
            identity=framework_openai_identity(),
            evidence_class="native_evidence",
            exposure_state="unexposed",
            environment_fingerprint_sha256="a" * 64,
        ),
    )


def test_fault_retains_partial_evidence_closes_and_does_not_invent_terminal(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    telemetry = InMemoryTelemetry()
    adapter = DirectEngineAdapter(
        store,
        ledger,
        telemetry,
        DirectEngineGraphClaimRegistry(),
        unpaid_runtime=UnpaidEngineRuntime(
            FaultingEngine, lambda attempt: attempt.framework.to_document()
        ),
    )
    attempt = _attempt(tmp_path)
    FaultingEngine.graph_path = str(attempt.isolation.graph_path)
    FaultingEngine.fault_method = "search"

    with pytest.raises(DirectEngineBoundaryError):
        asyncio.run(adapter.execute(attempt))

    assert FaultingEngine.closed
    failure_link = next(link for link in ledger.artifact_links if link.purpose == "engine_search")
    failure = json.loads(store.open_verified(failure_link.artifact_ref))
    assert failure["method"] == "search"
    assert failure["outcome"] == "failed"
    assert failure["failure_classification"] == "engine_call_failed"
    assert failure["failure_code"] == "engine_call_failed"
    assert failure["exception_type"] == "builtins.RuntimeError"
    assert len(failure["exception_type_sha256"]) == 64
    assert failure["duration_ms"] >= 0
    assert failure["partial_evidence_sha256"]
    assert failure["usage"] == {"calls": 0, "tokens": 0, "usd": 0, "wall_seconds": 0}
    assert "synthetic secret" not in json.dumps(failure)
    close_link = next(link for link in ledger.artifact_links if link.purpose == "engine_close")
    close = json.loads(store.open_verified(close_link.artifact_ref))
    assert close["outcome"] == "succeeded"
    assert any(link.purpose == "engine_note" for link in ledger.artifact_links)
    assert ledger.attempt_terminals == ()
    assert len(ledger.exposure_records) == 1
    assert ledger.exposure_records[0].decision.classification is ExposureClassification.EXPOSED
    assert ledger.provenance == telemetry.provenance == store.provenance == "unpaid_conformance"
    assert not ledger.eligible_for_paid_or_study
    assert not telemetry.eligible_for_paid_or_study
    assert not store.eligible_for_paid_or_study
    assert {link.logical_ledger for link in ledger.cost_ledger_entries_for(attempt.attempt_id)} == {
        "framework_openai",
        "local_resources",
    }
    assert {span.span_class for span in telemetry.semantic_spans} >= {
        SpanClass.ARTIFACT_PERSISTENCE,
        SpanClass.DAEMON_DISPATCH,
        SpanClass.FRAMEWORK_EXTRACTION,
        SpanClass.MEMORY_WRITE,
    }


def test_engine_success_publishes_separate_framework_and_local_quantity_ledgers(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    attempt = replace(_attempt(tmp_path, "success.db"), search_query=None)
    FaultingEngine.graph_path = str(attempt.isolation.graph_path)
    FaultingEngine.fault_method = "none"
    adapter = DirectEngineAdapter(
        store,
        ledger,
        InMemoryTelemetry(),
        DirectEngineGraphClaimRegistry(),
        unpaid_runtime=UnpaidEngineRuntime(
            FaultingEngine, lambda value: value.framework.to_document()
        ),
    )

    asyncio.run(adapter.execute(attempt))

    links = ledger.cost_ledger_entries_for(attempt.attempt_id)
    assert {link.logical_ledger for link in links} == {"framework_openai", "local_resources"}
    assert all(store.open_verified(link.artifact_ref) for link in links)


def test_graph_claim_is_unique_and_product_graph_is_rejected(tmp_path: Path) -> None:
    registry = DirectEngineGraphClaimRegistry()
    first = _attempt(tmp_path)
    registry.claim(first.isolation.graph_path_digest, first.attempt_id)

    with pytest.raises(DirectEngineBoundaryError, match="engine graph reuse denied"):
        registry.claim(first.isolation.graph_path_digest, AttemptId.new())
    with pytest.raises(DirectEngineBoundaryError, match="product graph access denied"):
        DirectEngineIsolation(
            RuntimeId.new(),
            tmp_path / "product-home",
            tmp_path / "product-home" / "graph.db",
            (tmp_path / "product-home" / "graph.db",),
        )


def test_real_engine_path_requires_explicit_live_envelope_and_usage_meter(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    attempt = replace(
        _attempt(tmp_path),
        execution_mode=DirectEngineExecutionMode.LIVE_CONFORMANCE,
        live_envelope=LiveEngineEnvelope(8, 1000, 1.0, 30.0),
    )
    adapter = DirectEngineAdapter(
        store,
        ledger,
        InMemoryTelemetry(),
        DirectEngineGraphClaimRegistry(),
    )

    with pytest.raises(DirectEngineBoundaryError, match="live engine usage meter required"):
        asyncio.run(adapter.execute(attempt))

    assert ledger.artifact_links == ()


@pytest.mark.parametrize(
    ("method", "expected_outcome", "expected_code"),
    (
        ("health", "failed", "engine_call_failed"),
        ("note", "timed_out", "engine_call_timed_out"),
        ("detail", "failed", "engine_call_failed"),
        ("close", "failed", "engine_call_failed"),
    ),
)
def test_each_engine_boundary_failure_persists_typed_retrievable_evidence(
    tmp_path: Path,
    method: str,
    expected_outcome: str,
    expected_code: str,
) -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    attempt = replace(
        _attempt(tmp_path, f"{method}.db"),
        search_query=None if method in {"health", "note"} else "fact",
        detail_node_uuid="node-1" if method == "detail" else None,
    )
    FaultingEngine.graph_path = str(attempt.isolation.graph_path)
    FaultingEngine.fault_method = method
    telemetry = InMemoryTelemetry()
    adapter = DirectEngineAdapter(
        store,
        ledger,
        telemetry,
        DirectEngineGraphClaimRegistry(),
        unpaid_runtime=UnpaidEngineRuntime(
            FaultingEngine, lambda value: value.framework.to_document()
        ),
    )

    with pytest.raises(DirectEngineBoundaryError):
        asyncio.run(adapter.execute(attempt))

    link = next(link for link in ledger.artifact_links if link.purpose == f"engine_{method}")
    document = json.loads(store.open_verified(link.artifact_ref))
    assert document["method"] == method
    assert document["outcome"] == expected_outcome
    assert document["failure_code"] == expected_code
    assert "synthetic secret" not in json.dumps(document)
    assert any(
        span.context.failure_code == expected_code
        for span in telemetry.semantic_spans
        if span.span_class
        in {
            SpanClass.DAEMON_DISPATCH,
            SpanClass.MEMORY_WRITE,
            SpanClass.MEMORY_RETRIEVAL,
            SpanClass.CLEANUP,
        }
    )
    if method == "close":
        assert any(link.purpose == "engine_note" for link in ledger.artifact_links)


def test_cancellation_propagates_and_shielded_cleanup_records_success(tmp_path: Path) -> None:
    async def scenario() -> tuple[asyncio.Task[object], dict[str, object], InMemoryTelemetry]:
        store = InMemoryArtifactStore()
        ledger = InMemoryLedger()
        attempt = _attempt(tmp_path, "cancel.db")
        FaultingEngine.graph_path = str(attempt.isolation.graph_path)
        FaultingEngine.fault_method = "cancel"
        FaultingEngine.blocker = asyncio.Event()
        telemetry = InMemoryTelemetry()
        adapter = DirectEngineAdapter(
            store,
            ledger,
            telemetry,
            DirectEngineGraphClaimRegistry(),
            unpaid_runtime=UnpaidEngineRuntime(
                FaultingEngine, lambda value: value.framework.to_document()
            ),
        )
        task = asyncio.create_task(adapter.execute(attempt))
        while not any(link.purpose == "engine_note" for link in ledger.artifact_links):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        search_link = next(
            link for link in ledger.artifact_links if link.purpose == "engine_search"
        )
        close_link = next(link for link in ledger.artifact_links if link.purpose == "engine_close")
        return (
            task,
            {
                "search": json.loads(store.open_verified(search_link.artifact_ref)),
                "close": json.loads(store.open_verified(close_link.artifact_ref)),
            },
            telemetry,
        )

    task, evidence, telemetry = asyncio.run(scenario())

    assert task.cancelled()
    assert FaultingEngine.closed
    assert evidence["search"]["outcome"] == "cancelled"
    assert evidence["search"]["failure_code"] == "engine_call_cancelled"
    assert evidence["close"]["outcome"] == "succeeded"
    assert any(
        span.span_class is SpanClass.MEMORY_RETRIEVAL
        and span.context.failure_code == "engine_call_cancelled"
        for span in telemetry.semantic_spans
    )


def test_unpaid_mode_rejects_unlabeled_or_real_runtime_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt = _attempt(tmp_path, "poc.db")
    provider_called = False

    class RealProviderPoC(FaultingEngine):
        @classmethod
        async def from_config(cls, cfg: object) -> RealProviderPoC:
            nonlocal provider_called
            provider_called = True
            return cls()

    unlabeled = DirectEngineAdapter(
        InMemoryArtifactStore(),
        InMemoryLedger(),
        InMemoryTelemetry(),
        DirectEngineGraphClaimRegistry(),
    )
    with pytest.raises(DirectEngineBoundaryError, match="unpaid engine capability required"):
        asyncio.run(unlabeled.execute(attempt))

    import memrelay_eval.adapters.memrelay.engine as engine_adapter

    monkeypatch.setattr(engine_adapter, "_load_memory_engine", lambda: RealProviderPoC)
    monkeypatch.setattr(
        engine_adapter,
        "_build_memrelay_config",
        lambda value: value.framework.to_document(),
    )
    with pytest.raises(DirectEngineBoundaryError, match="unpaid engine capability required"):
        asyncio.run(unlabeled.execute(replace(attempt, attempt_id=AttemptId.new())))

    assert not provider_called
