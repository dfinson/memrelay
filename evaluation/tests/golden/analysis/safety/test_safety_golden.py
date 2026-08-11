from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from memrelay_eval.analysis.safety import (
    DetectorInspection,
    InjectedPositive,
    SafetyOpportunity,
    SafetyPolicy,
    evaluate_safety,
)


def test_zero_event_safety_report_matches_frozen_golden_and_schema() -> None:
    policy = SafetyPolicy(
        policy_id="safety-policy-v1",
        detector_id="detector-v1",
        detector_version="1.0.0",
        injected_positive_plan_sha256="a" * 64,
        sensitivity_model_sha256="b" * 64,
        threshold_sha256="c" * 64,
    )
    document = evaluate_safety(
        policy=policy,
        opportunities=(
            SafetyOpportunity(
                "opportunity-1",
                "SG-001",
                "assignment-1",
                "product",
                "controlled",
                "included",
                ("d" * 64,),
            ),
            SafetyOpportunity(
                "opportunity-2",
                "SG-001",
                "assignment-2",
                "product",
                "controlled",
                "excluded",
                ("d" * 64,),
            ),
        ),
        inspections=(
            DetectorInspection("opportunity-1", "detector-v1", "1.0.0", "no_event", ("e" * 64,)),
            DetectorInspection("opportunity-2", "detector-v1", "1.0.0", "no_event", ("f" * 64,)),
        ),
        injected_positives=(
            InjectedPositive(
                "injection-1",
                "detector-v1",
                "1.0.0",
                True,
                "a" * 64,
                ("0" * 64,),
            ),
        ),
        source_manifest_sha256=("9" * 64,),
    ).to_document()
    root = Path(__file__).parents[4]
    expected = json.loads((Path(__file__).parent / "safety-zero-event.json").read_text("utf-8"))
    schema = json.loads((root / "schemas" / "safety-report.schema.json").read_text("utf-8"))

    jsonschema.validate(document, schema)
    assert document == expected
