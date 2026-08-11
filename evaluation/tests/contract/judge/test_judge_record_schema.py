from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from memrelay_eval.scoring.rubric import JUDGE_CRITERIA, JudgeCriterionScore, JudgeRecord


def test_judge_record_matches_the_versioned_schema() -> None:
    citation = f"artifact://blinded/{'d' * 64}"
    record = JudgeRecord(
        candidate_id="candidate-a",
        view_sha256="a" * 64,
        panel_protocol_sha256="b" * 64,
        schedule_position=0,
        judge_slot=1,
        model_id="judge-one",
        model_family="family-one",
        diversity_label="diverse",
        requires_stronger_calibration=False,
        runtime_lock_sha256="c" * 64,
        model_lock_sha256="d" * 64,
        rubric_sha256="e" * 64,
        system_prompt_sha256="f" * 64,
        tools_sha256="0" * 64,
        decoding_controls_sha256="1" * 64,
        status="completed",
        criteria={name: JudgeCriterionScore(0.5, 0.2, (citation,)) for name in JUDGE_CRITERIA},
    )
    schema_path = Path(__file__).parents[3] / "schemas" / "judge-record.schema.json"

    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(
        record.document()
    )
