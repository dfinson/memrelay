from __future__ import annotations

import asyncio
from hashlib import sha256

from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.scoring.adjudication import FrozenDisagreementAdjudicator
from memrelay_eval.scoring.rubric import JudgeRuntimeResult

from ...unit.judge.test_adjudication import _adjudicator, _records, _view

_PATCH_PATH = b"C:\\memrelay\\patch.diff"


def test_multiple_disputed_criteria_complete_in_one_bounded_session() -> None:
    store = InMemoryArtifactStore()

    class Process:
        calls = 0

        async def run_judge_session(self, request: object) -> object:
            self.calls += 1
            citation = f"artifact://blinded/{sha256(_PATCH_PATH).hexdigest()}"
            return JudgeRuntimeResult(
                "completed",
                {
                    "resolutions": {
                        name: {
                            "score": 0.6,
                            "resolution": "Artifact supports the resolution.",
                            "uncertainty": 0.2,
                            "citations": [citation],
                        }
                        for name in request.disputed_criteria
                    }
                },
                tokens=100,
                tool_calls=3,
                active_seconds=10,
                wall_seconds=20,
            )

    process = Process()
    adjudicator: FrozenDisagreementAdjudicator = _adjudicator(store, process)
    view = _view(store)
    result = asyncio.run(
        adjudicator.adjudicate("candidate-a", view, _records((0.1, 0.8, 0.5), view.sha256))
    )

    assert result.record.status == "completed"
    assert process.calls == 1
