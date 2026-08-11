from __future__ import annotations

import asyncio

from memrelay_eval.adapters.fakes import InMemoryArtifactStore

from ...unit.judge.test_adjudication import FakeJudgeProcess, _adjudicator, _records, _view


def test_adjudicator_input_omits_judge_identity_and_unblinded_context() -> None:
    store = InMemoryArtifactStore()
    process = FakeJudgeProcess()
    view = _view(store)

    result = asyncio.run(
        _adjudicator(store, process).adjudicate(
            "candidate-a", view, _records((0.1, 0.8, 0.5), view.sha256)
        )
    )

    prompt = process.requests[0].prompt.casefold()
    assert result.record.status == "completed"
    assert "judge-1" not in prompt
    assert "family-1" not in prompt
    assert "candidate-a" not in prompt
    assert "openai" not in prompt
    # This text is an instruction; no credential material reaches the prompt.
    assert "credential" in prompt
