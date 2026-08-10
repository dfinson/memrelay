"""Integration coverage for the shipped daemon plus MCP product stratum."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.adapters.memrelay import (
    MemrelayProductTreatment,
    ProductProvisionRequest,
    build_framework_process_environments,
    shipped_observation_path,
)

from memrelay.daemon.protocol import StubBackend

pytestmark = pytest.mark.integration


async def _run_product(tmp_path: Path) -> tuple[object, object, bytes, bytes]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    daemon_env, agent_env, mcp_env = build_framework_process_environments()
    request = ProductProvisionRequest(
        home_path=home,
        workspace_root=workspace,
        namespace="dfinson",
        repo="owner/repo",
        backend=StubBackend(),
        daemon_environment=daemon_env,
        agent_environment=agent_env,
        mcp_environment=mcp_env,
        probe_query="product stratum recall",
        probe_node_uuid="probe-node-1",
        probe_note="product stratum note",
    )
    store = InMemoryArtifactStore()
    treatment = MemrelayProductTreatment(artifact_store=store)
    handle = await treatment.provision(request)
    refs = await treatment.collect_state(handle)
    state_doc = json.loads(store.open_verified(refs[0]).decode("utf-8"))
    await treatment.close(handle)
    cleanup_doc = json.loads(store.open_verified(handle.evidence_refs[-1]).decode("utf-8"))
    return (
        state_doc,
        cleanup_doc,
        state_doc["tool_calls"][0]["text"].encode(),
        cleanup_doc["endpoint"].encode(),
    )


def test_product_treatment_round_trips_through_shipped_daemon(tmp_path: Path) -> None:
    state_doc, cleanup_doc, _tool_text, _endpoint = asyncio.run(_run_product(tmp_path))

    assert state_doc["preflight"]["ready"] is True
    assert state_doc["tool_visibility"]["exact"] is True
    assert {call["result_kind"] for call in state_doc["tool_calls"]} == {"success"}
    assert {call["tool_name"] for call in state_doc["tool_calls"]} == {
        "memory_recall",
        "memory_detail",
        "memory_note",
    }
    assert state_doc["daemon_health"]["status"] == "running"
    assert state_doc["observation_path"] == str(shipped_observation_path(Path(tmp_path) / "home"))
    assert cleanup_doc["daemon_stopped"] is True
    assert cleanup_doc["mcp_closed"] is True
