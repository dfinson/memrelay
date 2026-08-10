"""Fake-only integration coverage of evaluator-owned product evidence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from evaluation.tests.contract.memrelay.test_daemon import _Launcher, _LiveHealthClient, _request
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.adapters.memrelay import MemrelayProductTreatment
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.orchestration.attempt import AttemptTerminalRecorder, ProductTreatmentAttempt


def test_fake_product_attempt_preserves_observation_and_cleanup_evidence(tmp_path: Path) -> None:
    launcher = _Launcher()
    store = InMemoryArtifactStore()
    treatment = MemrelayProductTreatment(
        artifact_store=store,
        launcher=launcher,  # type: ignore[arg-type]
        health_client_factory=lambda _: _LiveHealthClient(),
    )
    handle = asyncio.run(treatment.provision(_request(tmp_path, attempt_id="attempt-integration")))
    refs = asyncio.run(treatment.collect_state(handle))
    state = json.loads(store.open_verified(refs[0]))
    asyncio.run(treatment.close(handle))
    cleanup = json.loads(store.open_verified(handle.evidence_refs[-1]))

    assert state["observation_artifact_exists"] is True
    assert Path(state["observation_path"]).is_file()
    assert cleanup["process"]["process_tree_stopped"] is True


def test_fake_product_attempt_rejects_fourth_tool_before_evidence(tmp_path: Path) -> None:
    async def four_tools() -> tuple[str, ...]:
        return ("memory_recall", "memory_detail", "memory_note", "spoof")

    treatment = MemrelayProductTreatment(
        launcher=_Launcher(),  # type: ignore[arg-type]
        health_client_factory=lambda _: _LiveHealthClient(),
    )
    handle = asyncio.run(treatment.provision(_request(tmp_path, mcp_tool_surface_probe=four_tools)))
    with pytest.raises(ConformancePauseError):
        asyncio.run(treatment.collect_state(handle))
    asyncio.run(treatment.close(handle))


def test_product_attempt_uses_existing_ledger_claim_and_telemetry(tmp_path: Path) -> None:
    ledger = InMemoryLedger()
    telemetry = InMemoryTelemetry()
    treatment = MemrelayProductTreatment(
        launcher=_Launcher(),  # type: ignore[arg-type]
        health_client_factory=lambda _: _LiveHealthClient(),
    )
    stage = ProductTreatmentAttempt(
        treatment,
        AttemptTerminalRecorder(ledger, telemetry),
        telemetry,
    )
    handle = asyncio.run(
        stage.provision(_request(tmp_path), attempt_id=AttemptId.new(), run_id=RunId.new())
    )
    references = asyncio.run(stage.collect_and_close(handle))

    assert references
    assert telemetry.observations[0].event_name == "attempt_started"
