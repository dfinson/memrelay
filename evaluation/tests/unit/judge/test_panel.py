from __future__ import annotations

import asyncio
import json
from hashlib import sha256

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import (
    ConformancePauseError,
    JudgePanelConformanceError,
    ProcessLaunchError,
    UnqualifiedEvidencePortError,
)
from memrelay_eval.scoring.blinding import BlindingPolicy, generate_blinded_view
from memrelay_eval.scoring.rubric import (
    JUDGE_CRITERIA,
    FrozenJudgeRubric,
    FrozenPanelSchedule,
    JudgeLimits,
    JudgeRuntimeResult,
)
from memrelay_eval.scoring.service import JudgePanelRunner, select_judge_slots


def _model_lock() -> dict[str, object]:
    capability = {
        "tools": True,
        "permissions": True,
        "context": 1,
        "events": True,
        "cancellation": True,
        "sessions": True,
    }
    return {
        "lock_sha256": "a" * 64,
        "runtime_lock_sha256": "b" * 64,
        "selected_models": [
            {
                "role": "M0",
                "native_id": "generator",
                "family": "generator",
                "capabilities": capability,
            },
            {
                "role": "judge",
                "native_id": "judge-one",
                "family": "family-one",
                "capabilities": capability,
                "reasoning_effort": "high",
                "context_tier": "large",
            },
            {
                "role": "judge",
                "native_id": "judge-two",
                "family": "family-two",
                "capabilities": capability,
                "reasoning_effort": "high",
                "context_tier": "large",
            },
            {
                "role": "judge",
                "native_id": "judge-three",
                "family": "family-three",
                "capabilities": capability,
                "reasoning_effort": "high",
                "context_tier": "large",
            },
        ],
    }


def _schedule(*, stage_token_limit: int = 1200) -> FrozenPanelSchedule:
    limits = JudgeLimits(
        per_session_tokens=100,
        per_session_tools=3,
        per_session_active_seconds=10,
        per_session_wall_seconds=20,
        stage_session_limit=12,
        stage_token_limit=stage_token_limit,
        stage_tool_limit=36,
        stage_active_seconds_limit=120,
        stage_wall_seconds_limit=240,
    )
    return FrozenPanelSchedule(
        ("candidate-a",),
        ("calibration-a",),
        ("duplicate-a",),
        ("sentinel-a",),
        sha256(b"17").hexdigest(),
        limits,
    )


def _view(store: InMemoryArtifactStore):
    source = store.put_bytes(
        json.dumps(
            {
                "requirements": "Preserve documented behavior.",
                "code": {"file": "src/feature.py", "content": "def feature(): return True"},
                "artifact_locations": {"patch": "C:\\memrelay\\patch.diff"},
            }
        ).encode(),
        media_type="application/json",
        classification="synthetic",
    )
    return generate_blinded_view(store, source, BlindingPolicy())


def _response() -> dict[str, object]:
    citation = "artifact://blinded/" + sha256(b"C:\\memrelay\\patch.diff").hexdigest()
    return {
        "criteria": {
            name: {"score": 0.75, "uncertainty": 0.1, "citations": [citation]}
            for name in JUDGE_CRITERIA
        }
    }


class FakeJudgeRuntime:
    def __init__(self, results: list[JudgeRuntimeResult] | None = None) -> None:
        self.requests = []
        self._results = results or [JudgeRuntimeResult("completed", _response()) for _ in range(3)]

    async def run_judge_session(self, session: object) -> object:
        self.requests.append(session)
        return self._results.pop(0)


def _runner(store: InMemoryArtifactStore, runtime: FakeJudgeRuntime) -> JudgePanelRunner:
    return JudgePanelRunner(
        store,
        runtime,
        _model_lock(),
        "b" * 64,
        FrozenJudgeRubric(),
        _schedule(),
        17,
    )


def test_panel_runs_exactly_three_fresh_blinded_sessions_and_replays_retained_records() -> None:
    store = InMemoryArtifactStore()
    runtime = FakeJudgeRuntime()
    view = _view(store)
    runner = _runner(store, runtime)

    outcome = asyncio.run(runner.judge("candidate-a", view.view_artifact))
    replay = asyncio.run(runner.judge("candidate-a", view.view_artifact))

    assert outcome.is_complete
    assert replay is outcome
    assert len(runtime.requests) == 3
    assert len({request.session_id for request in runtime.requests}) == 3
    assert {request.model_id for request in runtime.requests} == {
        "judge-one",
        "judge-two",
        "judge-three",
    }
    assert all(request.tools[0]["read_only"] is True for request in runtime.requests)
    assert all("assignment" not in request.prompt for request in runtime.requests)
    assert all("OPENAI_API_KEY" not in request.prompt for request in runtime.requests)
    assert all(record.diversity_label == "diverse" for record in outcome.records)
    assert all(record.status == "completed" for record in outcome.records)


def test_panel_retains_failures_and_blocks_partial_completion_without_replacement() -> None:
    store = InMemoryArtifactStore()
    runtime = FakeJudgeRuntime(
        [
            JudgeRuntimeResult("completed", _response()),
            JudgeRuntimeResult("failed", None, failure_code="judge_timeout"),
            JudgeRuntimeResult("completed", {"criteria": {}}),
        ]
    )

    outcome = asyncio.run(_runner(store, runtime).judge("candidate-a", _view(store).view_artifact))

    assert not outcome.is_complete
    assert outcome.blocking_code == "judge_panel_incomplete"
    assert [record.status for record in outcome.records] == ["completed", "failed", "failed"]
    assert [record.failure_code for record in outcome.records[1:]] == [
        "judge_timeout",
        "judge_response_schema_invalid",
    ]
    assert len(runtime.requests) == 3


def test_missing_evidence_is_an_explicit_unavailable_panel_outcome_without_calls() -> None:
    store = InMemoryArtifactStore()
    runtime = FakeJudgeRuntime()

    outcome = asyncio.run(
        _runner(store, runtime).judge(
            "candidate-a", _view(store).view_artifact, evidence_available=False
        )
    )

    assert not outcome.is_complete
    assert [record.status for record in outcome.records] == ["unavailable"] * 3
    assert not runtime.requests


def test_slot_selection_rejects_partial_or_duplicate_panels_and_labels_homogeneous_scarcity() -> (
    None
):
    lock = _model_lock()
    lock["selected_models"] = lock["selected_models"][:-1]
    with pytest.raises(JudgePanelConformanceError, match="exactly three"):
        select_judge_slots(lock, "b" * 64)

    lock = _model_lock()
    for model in lock["selected_models"][1:]:
        model["family"] = "shared"
    _, diversity = select_judge_slots(lock, "b" * 64)
    assert diversity.label == "homogeneous"
    assert diversity.requires_stronger_calibration


def test_fake_artifacts_are_required_for_judge_panels() -> None:
    class PaidStore(InMemoryArtifactStore):
        provenance = "durable"
        eligible_for_paid_or_study = True

    with pytest.raises(UnqualifiedEvidencePortError):
        _runner(PaidStore(), FakeJudgeRuntime())


def test_runtime_pause_is_retained_and_replay_cannot_reexecute_the_panel() -> None:
    class PausedRuntime:
        def __init__(self) -> None:
            self.calls = 0

        async def run_judge_session(self, _session: object) -> object:
            self.calls += 1
            raise ConformancePauseError("sdk_unavailable", "SDK unavailable")

    store = InMemoryArtifactStore()
    runtime = PausedRuntime()
    runner = _runner(store, runtime)
    view = _view(store)

    first = asyncio.run(runner.judge("candidate-a", view.view_artifact))
    replay = asyncio.run(runner.judge("candidate-a", view.view_artifact))

    assert [record.failure_code for record in first.records] == ["sdk_unavailable"] * 3
    assert replay is first
    assert runtime.calls == 3


def test_missing_view_becomes_a_retained_unavailable_panel_before_any_call() -> None:
    store = InMemoryArtifactStore()
    runtime = FakeJudgeRuntime()
    runner = _runner(store, runtime)

    outcome = asyncio.run(runner.judge("candidate-a", ArtifactRef.from_bytes(b"missing")))

    assert [record.status for record in outcome.records] == ["unavailable"] * 3
    assert not runtime.requests


def test_direct_leak_in_a_forged_view_becomes_unavailable_without_a_judge_call() -> None:
    store = InMemoryArtifactStore()
    forged = store.put_bytes(
        (
            b'{"schema_version":"1.0.0","source":{"artifact_id":"art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            b'"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},'
            b'"policy_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
            b'"transform_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
            b'"evidence":{"assignment":"treatment"},"artifact_locations":{}}'
        ),
        media_type="application/json",
        classification="synthetic",
    )
    runtime = FakeJudgeRuntime()

    outcome = asyncio.run(_runner(store, runtime).judge("candidate-a", forged))

    assert [record.status for record in outcome.records] == ["unavailable"] * 3
    assert not runtime.requests


def test_stage_budget_reserves_each_remaining_session_before_another_provider_call() -> None:
    store = InMemoryArtifactStore()
    runtime = FakeJudgeRuntime(
        [
            JudgeRuntimeResult("completed", _response(), tokens=100),
            JudgeRuntimeResult("completed", _response(), tokens=100),
            JudgeRuntimeResult("completed", _response(), tokens=100),
        ]
    )
    runner = JudgePanelRunner(
        store,
        runtime,
        _model_lock(),
        "b" * 64,
        FrozenJudgeRubric(),
        _schedule(stage_token_limit=250),
        17,
    )

    outcome = asyncio.run(runner.judge("candidate-a", _view(store).view_artifact))

    assert [record.status for record in outcome.records] == [
        "completed",
        "completed",
        "unavailable",
    ]
    assert outcome.records[-1].failure_code == "judge_stage_cap_exhausted"
    assert len(runtime.requests) == 2


def test_judging_rejects_a_candidate_outside_the_next_sealed_position() -> None:
    store = InMemoryArtifactStore()
    runner = _runner(store, FakeJudgeRuntime())

    with pytest.raises(JudgePanelConformanceError, match="candidate order not sealed"):
        asyncio.run(runner.judge("calibration-a", _view(store).view_artifact))


def test_process_launch_failure_is_retained_for_all_authorized_judge_slots() -> None:
    class FailingProcess:
        async def run_judge_session(self, _session: object) -> object:
            raise ProcessLaunchError("process_start_failed")

    store = InMemoryArtifactStore()
    runner = _runner(store, FailingProcess())
    view = _view(store)

    outcome = asyncio.run(runner.judge("candidate-a", view.view_artifact))

    assert [record.failure_code for record in outcome.records] == [
        "judge_process_launch_failed"
    ] * 3
