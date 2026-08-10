from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.adapters.memrelay.engine import (
    DirectEngineAdapter,
    DirectEngineAttempt,
    UnpaidEngineRuntime,
)
from memrelay_eval.domain.engine import (
    DirectEngineIsolation,
    FrameworkConfiguration,
    RenderingContract,
    StratumAuthority,
)
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
from memrelay_eval.domain.states import EvaluationStratum
from memrelay_eval.orchestration.control import DirectEngineGraphClaimRegistry


class ApiSpy:
    provenance = "unpaid_conformance"
    eligible_for_paid_or_study = False
    calls: list[tuple[str, object]] = []
    instance: ApiSpy | None = None
    graph_path: str = ""

    @classmethod
    async def from_config(cls, cfg: object) -> ApiSpy:
        cls.calls.append(("from_config", cfg))
        cls.instance = cls()
        return cls.instance

    async def health(self) -> dict[str, object]:
        self.calls.append(("health", None))
        return {
            "status": "ok",
            "backend": "ladybug",
            "graph_path": self.graph_path,
            "llm_strategy": "byo-key",
            "embeddings_provider": "local",
            "embeddings_model": "BAAI/bge-small-en-v1.5",
        }

    async def note(
        self,
        content: str,
        namespace: str,
        repo: str | None = None,
        source: str | None = None,
    ) -> str:
        self.calls.append(("note", (content, namespace, repo, source)))
        return "episode-1"

    async def search(
        self,
        query: str,
        namespace: str,
        prefer_repo: str | None = None,
        *,
        prefer_agent: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(("search", (query, namespace, prefer_repo, prefer_agent)))
        return {
            "nodes": [{"uuid": "node-1", "name": "Decision", "summary": "Use public APIs"}],
            "edges": [],
            "scores": [0.9],
        }

    async def detail(self, node_uuid: str, namespace: str) -> dict[str, object]:
        self.calls.append(("detail", (node_uuid, namespace)))
        return {"node": {"uuid": node_uuid}, "connected_edges": [], "episodes": []}

    async def close(self) -> None:
        self.calls.append(("close", None))


def _authority(
    assignment_id: AssignmentId, run_id: RunId, runtime_id: RuntimeId
) -> StratumAuthority:
    return StratumAuthority(
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


def _attempt(tmp_path: Path) -> DirectEngineAttempt:
    assignment_id = AssignmentId.new()
    run_id = RunId.new()
    runtime_id = RuntimeId.new()
    home = tmp_path / "engine-home"
    return DirectEngineAttempt(
        AttemptId.new(),
        run_id,
        assignment_id,
        _authority(assignment_id, run_id, runtime_id),
        DirectEngineIsolation(runtime_id, home, home / "graph.db", (tmp_path / "product.db",)),
        FrameworkConfiguration(),
        RenderingContract("1.0.0", "- {name}: {summary}", "No memories."),
        "opaque-namespace",
        note_content="Remember the decision",
        note_repo="owner/repo",
        note_source="copilot",
        search_query="decision",
        search_prefer_repo="owner/repo",
        search_prefer_agent="copilot",
        detail_node_uuid="node-1",
    )


def test_direct_engine_calls_only_public_async_api_and_converts_plain_dicts(
    tmp_path: Path,
) -> None:
    ApiSpy.calls = []
    attempt = _attempt(tmp_path)
    ApiSpy.graph_path = str(attempt.isolation.graph_path)
    sentinel_config = object()
    ledger = InMemoryLedger()
    adapter = DirectEngineAdapter(
        InMemoryArtifactStore(),
        ledger,
        InMemoryTelemetry(),
        DirectEngineGraphClaimRegistry(),
        unpaid_runtime=UnpaidEngineRuntime(ApiSpy, lambda value: sentinel_config),
    )

    evidence = asyncio.run(adapter.execute(attempt))

    assert [name for name, _ in ApiSpy.calls] == [
        "from_config",
        "health",
        "note",
        "search",
        "detail",
        "close",
    ]
    assert ApiSpy.calls[0][1] is sentinel_config
    assert evidence.health is not None
    assert evidence.search is not None
    assert evidence.detail is not None
    assert evidence.search.payload["nodes"] == (
        {"uuid": "node-1", "name": "Decision", "summary": "Use public APIs"},
    )
    assert evidence.rendered_search == "- Decision: Use public APIs"
    assert {
        "stratum_authority",
        "engine_graph",
        "engine_rendering_contract",
        "engine_cost_identity",
        "engine_construction",
        "engine_health",
        "engine_note",
        "engine_search",
        "engine_rendered_search",
        "engine_detail",
        "engine_close",
        "engine_consumed_usage",
    }.issubset({link.purpose for link in ledger.artifact_links})
    with pytest.raises(TypeError):
        evidence.search.payload["nodes"] = ()  # type: ignore[index]
