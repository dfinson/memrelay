from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.adapters.memrelay.engine import (
    DirectEngineAdapter,
    DirectEngineAttempt,
)
from memrelay_eval.domain.engine import (
    DirectEngineExecutionMode,
    DirectEngineIsolation,
    FrameworkConfiguration,
    LiveEngineEnvelope,
    RenderingContract,
    StratumAuthority,
)
from memrelay_eval.domain.errors import DirectEngineBoundaryError
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
    closed = False
    graph_path = ""

    @classmethod
    async def from_config(cls, cfg: object) -> FaultingEngine:
        del cfg
        cls.closed = False
        return cls()

    async def health(self) -> dict[str, object]:
        return {"status": "ok", "graph_path": self.graph_path}

    async def note(
        self, content: str, namespace: str, repo: str | None = None, source: str | None = None
    ) -> str:
        del content, namespace, repo, source
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
        raise RuntimeError("synthetic search fault")

    async def detail(self, node_uuid: str, namespace: str) -> dict[str, object]:
        del node_uuid, namespace
        return {}

    async def close(self) -> None:
        type(self).closed = True


def _attempt(tmp_path: Path, graph_name: str = "graph.db") -> DirectEngineAttempt:
    assignment_id = AssignmentId.new()
    run_id = RunId.new()
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
        AttemptId.new(),
        run_id,
        assignment_id,
        authority,
        DirectEngineIsolation(runtime_id, home, home / graph_name, (tmp_path / "daemon.db",)),
        FrameworkConfiguration(),
        RenderingContract("1.0.0", "{name}: {summary}", "No memories."),
        "namespace",
        note_content="fact",
        search_query="fact",
    )


def test_fault_retains_partial_evidence_closes_and_does_not_invent_terminal(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    ledger = InMemoryLedger()
    telemetry = InMemoryTelemetry()
    adapter = DirectEngineAdapter(
        store,
        ledger,
        telemetry,
        DirectEngineGraphClaimRegistry(),
        engine_type=FaultingEngine,
        config_builder=lambda attempt: attempt.framework.to_document(),
    )
    attempt = _attempt(tmp_path)
    FaultingEngine.graph_path = str(attempt.isolation.graph_path)

    with pytest.raises(DirectEngineBoundaryError) as raised:
        asyncio.run(adapter.execute(attempt))

    assert FaultingEngine.closed
    assert len(raised.value.evidence) >= 8
    assert any(link.purpose == "engine_close" for link in ledger.artifact_links)
    assert ledger.attempt_terminals == ()
    assert len(ledger.exposure_records) == 1
    assert ledger.exposure_records[0].decision.classification is ExposureClassification.EXPOSED
    assert ledger.provenance == telemetry.provenance == store.provenance == "unpaid_conformance"
    assert not ledger.eligible_for_paid_or_study
    assert not telemetry.eligible_for_paid_or_study
    assert not store.eligible_for_paid_or_study


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
        engine_type=FaultingEngine,
        config_builder=lambda value: value.framework.to_document(),
    )

    with pytest.raises(DirectEngineBoundaryError, match="live engine usage meter required"):
        asyncio.run(adapter.execute(attempt))

    assert ledger.artifact_links == ()
