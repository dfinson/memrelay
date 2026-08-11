from __future__ import annotations

import asyncio
import json
from hashlib import sha256

from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.scoring.adjudication import (
    FrozenAdjudicationProtocol,
    FrozenAdjudicationRubric,
    FrozenDisagreementAdjudicator,
    FrozenDisagreementThreshold,
    evaluate_disagreement_thresholds,
)
from memrelay_eval.scoring.blinding import BlindingPolicy, generate_blinded_view
from memrelay_eval.scoring.rubric import (
    JUDGE_CRITERIA,
    JudgeCriterionScore,
    JudgeLimits,
    JudgeRecord,
    JudgeRuntimeResult,
)

_PATCH_PATH = b"C:\\memrelay\\patch.diff"


def _model_lock() -> dict[str, object]:
    capabilities = {
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
                "role": "judge",
                "native_id": "judge-one",
                "family": "family-one",
                "capabilities": capabilities,
                "reasoning_effort": "high",
                "context_tier": "large",
            }
        ],
    }


def _protocol(threshold: float = 0.2) -> FrozenAdjudicationProtocol:
    return FrozenAdjudicationProtocol(
        tuple(FrozenDisagreementThreshold(name, threshold) for name in JUDGE_CRITERIA),
        "judge-one",
        FrozenAdjudicationRubric(),
        JudgeLimits(100, 3, 10, 20, 1, 100, 3, 10, 20, 1),
        sha256(b"[1,2,3]").hexdigest(),
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
    return generate_blinded_view(store, source, BlindingPolicy()).view_artifact


def _records(
    scores: tuple[float, float, float] = (0.5, 0.5, 0.5),
    view_sha256: str = "d" * 64,
    candidate_id: str = "candidate-a",
) -> tuple[JudgeRecord, ...]:
    citation = f"artifact://blinded/{sha256(_PATCH_PATH).hexdigest()}"
    return tuple(
        JudgeRecord(
            candidate_id=candidate_id,
            view_sha256=view_sha256,
            panel_protocol_sha256="e" * 64,
            schedule_position=0,
            judge_slot=slot,
            model_id=f"judge-{slot}",
            model_family=f"family-{slot}",
            diversity_label="diverse",
            requires_stronger_calibration=False,
            runtime_lock_sha256="b" * 64,
            model_lock_sha256="a" * 64,
            rubric_sha256="f" * 64,
            system_prompt_sha256="0" * 64,
            tools_sha256="1" * 64,
            decoding_controls_sha256="2" * 64,
            status="completed",
            criteria={
                name: JudgeCriterionScore(scores[slot - 1], 0.1, (citation,))
                for name in JUDGE_CRITERIA
            },
        )
        for slot in (1, 2, 3)
    )


class FakeJudgeProcess:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.requests = []
        self.response = response

    async def run_judge_session(self, request: object) -> object:
        self.requests.append(request)
        return JudgeRuntimeResult("completed", self.response or _response())


def _response() -> dict[str, object]:
    citation = f"artifact://blinded/{sha256(_PATCH_PATH).hexdigest()}"
    return {
        "resolutions": {
            name: {
                "score": 0.6,
                "resolution": "The cited artifact supports this normalized score.",
                "uncertainty": 0.2,
                "citations": [citation],
            }
            for name in JUDGE_CRITERIA
        }
    }


def _adjudicator(
    store: InMemoryArtifactStore, process: FakeJudgeProcess, threshold: float = 0.2
) -> FrozenDisagreementAdjudicator:
    return FrozenDisagreementAdjudicator(
        store, process, _model_lock(), "b" * 64, _protocol(threshold)
    )


def test_every_threshold_is_retained_and_no_crossing_makes_zero_provider_calls() -> None:
    store = InMemoryArtifactStore()
    process = FakeJudgeProcess()
    view = _view(store)
    records = _records((0.4, 0.6, 0.5), view.sha256)

    evaluations = evaluate_disagreement_thresholds(records, _protocol(0.2))
    outcome = asyncio.run(
        _adjudicator(store, process, 0.2).adjudicate("candidate-a", view, records)
    )

    assert all(item.status == "evaluated" for item in evaluations.values())
    assert all(item.crossed is False for item in evaluations.values())  # equality does not cross.
    assert outcome.record.status == "not_triggered"
    assert not process.requests


def test_crossed_disputes_share_one_fresh_session_and_replay_never_calls_again() -> None:
    store = InMemoryArtifactStore()
    process = FakeJudgeProcess()
    adjudicator = _adjudicator(store, process)
    view = _view(store)
    records = _records((0.1, 0.8, 0.5), view.sha256)

    first = asyncio.run(adjudicator.adjudicate("candidate-a", view, records))
    replay = asyncio.run(adjudicator.adjudicate("candidate-a", view, records))

    assert first is replay
    assert first.record.status == "completed"
    assert set(first.record.resolutions) == set(JUDGE_CRITERIA)
    assert len(process.requests) == 1
    assert process.requests[0].response_contract == "adjudication"
    assert process.requests[0].disputed_criteria == JUDGE_CRITERIA
    assert all(record.sha256 in first.record.source_judge_record_sha256 for record in records)


def test_missing_or_unsealed_judge_inputs_block_before_any_provider_call() -> None:
    store = InMemoryArtifactStore()
    process = FakeJudgeProcess()

    outcome = asyncio.run(
        _adjudicator(store, process).adjudicate("candidate-a", _view(store), _records()[:2])
    )

    assert outcome.record.status == "blocked"
    assert outcome.blocking_code == "adjudication_judge_records_incomplete"
    assert all(item.status == "blocked" for item in outcome.record.threshold_evaluations.values())
    assert not process.requests


def test_malformed_or_source_mismatched_judge_inputs_block_before_any_provider_call() -> None:
    store = InMemoryArtifactStore()
    process = FakeJudgeProcess()
    view = _view(store)

    malformed = asyncio.run(
        _adjudicator(store, process).adjudicate("candidate-a", view, ("not-a-record",))  # type: ignore[arg-type]
    )
    source_mismatch = asyncio.run(
        _adjudicator(store, process).adjudicate(
            "candidate-b", view, _records((0.1, 0.8, 0.5), view.sha256)
        )
    )

    assert malformed.blocking_code == "adjudication_judge_records_incomplete"
    assert source_mismatch.blocking_code == "adjudication_source_binding_mismatch"
    assert not process.requests


def test_unavailable_model_and_hard_authority_gates_never_call_the_provider() -> None:
    store = InMemoryArtifactStore()
    process = FakeJudgeProcess()
    view = _view(store)
    records = _records((0.1, 0.8, 0.5), view.sha256)
    unavailable_lock = _model_lock()
    unavailable_lock["selected_models"] = []

    unavailable = FrozenDisagreementAdjudicator(
        store, process, unavailable_lock, "b" * 64, _protocol()
    )
    model_outcome = asyncio.run(unavailable.adjudicate("candidate-a", view, records))
    authority_outcome = asyncio.run(
        _adjudicator(store, process).adjudicate(
            "candidate-a",
            view,
            records,
            executable_passed=False,
            categorical_blockers=("security_blocker",),
        )
    )

    assert model_outcome.blocking_code == "adjudication_model_unavailable"
    assert authority_outcome.blocking_code == "adjudication_executable_authority_blocked"
    assert not process.requests


def test_cap_violation_is_retained_without_a_retry_or_fallback() -> None:
    store = InMemoryArtifactStore()
    process = FakeJudgeProcess()

    class OverCapProcess(FakeJudgeProcess):
        async def run_judge_session(self, request: object) -> object:
            self.requests.append(request)
            return JudgeRuntimeResult("completed", _response(), tokens=101)

    process = OverCapProcess()
    adjudicator = _adjudicator(store, process)
    view = _view(store)
    records = _records((0.1, 0.8, 0.5), view.sha256)
    first = asyncio.run(adjudicator.adjudicate("candidate-a", view, records))
    replay = asyncio.run(adjudicator.adjudicate("candidate-a", view, records))

    assert first is replay
    assert first.record.status == "failed"
    assert first.blocking_code == "adjudication_session_cap_exceeded"
    assert len(process.requests) == 1


def test_stage_cap_blocks_a_second_candidate_before_provider_invocation() -> None:
    store = InMemoryArtifactStore()
    process = FakeJudgeProcess()
    adjudicator = _adjudicator(store, process)
    first_view = _view(store)
    second_view = _view(store)

    first = asyncio.run(
        adjudicator.adjudicate(
            "candidate-a", first_view, _records((0.1, 0.8, 0.5), first_view.sha256)
        )
    )
    second = asyncio.run(
        adjudicator.adjudicate(
            "candidate-b",
            second_view,
            _records((0.1, 0.8, 0.5), second_view.sha256, "candidate-b"),
        )
    )

    assert first.record.status == "completed"
    assert second.blocking_code == "adjudication_stage_cap_exhausted"
    assert len(process.requests) == 1
