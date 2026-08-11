from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from jsonschema import Draft202012Validator
from memrelay_eval.scoring.adjudication import (
    AdjudicationRecord,
    AdjudicationResolution,
    FrozenAdjudicationProtocol,
    FrozenAdjudicationRubric,
    FrozenDisagreementThreshold,
    ThresholdEvaluation,
)
from memrelay_eval.scoring.rubric import JUDGE_CRITERIA, JudgeLimits


def test_adjudication_record_matches_versioned_schema() -> None:
    citation = f"artifact://blinded/{sha256(b'patch').hexdigest()}"
    protocol = FrozenAdjudicationProtocol(
        tuple(FrozenDisagreementThreshold(name, 0.1) for name in JUDGE_CRITERIA),
        "judge-a",
        FrozenAdjudicationRubric(),
        JudgeLimits(100, 3, 10, 20, 1, 100, 3, 10, 20),
        sha256(b"[1,2,3]").hexdigest(),
    )
    evaluations = {
        name: ThresholdEvaluation(name, 0.1, 0.2, True, "evaluated") for name in JUDGE_CRITERIA
    }
    record = AdjudicationRecord(
        "candidate-a",
        "0" * 64,
        "1" * 64,
        protocol.sha256,
        ("2" * 64, "3" * 64, "4" * 64),
        evaluations,
        "completed",
        "5" * 64,
        "6" * 64,
        protocol.rubric.sha256,
        protocol.rubric.system_prompt_sha256,
        protocol.rubric.tools_sha256,
        protocol.rubric.decoding_controls_sha256,
        sha256(b"[1,2,3]").hexdigest(),
        "judge-a",
        "family-a",
        {
            name: AdjudicationResolution(0.6, "Cited resolution.", 0.2, (citation,))
            for name in JUDGE_CRITERIA
        },
    )

    schema_path = Path(__file__).parents[3] / "schemas" / "adjudication-record.schema.json"
    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(
        record.document()
    )
