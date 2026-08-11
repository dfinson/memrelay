from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef, GraderResult
from memrelay_eval.domain.errors import JudgePanelConformanceError
from memrelay_eval.domain.states import GraderTerminalKind
from memrelay_eval.scoring.adjudication import AdjudicationRecord, ThresholdEvaluation
from memrelay_eval.scoring.outcomes import (
    CategoricalBlockerRecord,
    FrozenQualitativeAggregation,
    normalize_outcomes,
)
from memrelay_eval.scoring.reliability import (
    CriterionAgreement,
    GateDecision,
    PanelGateEvidence,
)
from memrelay_eval.scoring.rubric import JUDGE_CRITERIA, JudgeCriterionScore, JudgeRecord


def _artifact(value: bytes) -> ArtifactRef:
    return ArtifactRef.from_bytes(value)


def _grader(terminal: GraderTerminalKind = GraderTerminalKind.PASSED) -> GraderResult:
    passed = terminal is GraderTerminalKind.PASSED
    return GraderResult(
        "a" * 64,
        "b" * 64,
        terminal,
        passed if terminal in {GraderTerminalKind.PASSED, GraderTerminalKind.FAILED} else None,
        {"native": passed},
        1.0 if passed else 0.0,
        {"native": 1.0 if passed else 0.0},
        _artifact(b"raw"),
        _artifact(b"normalized"),
    )


def _records(*, score: float = 0.8) -> tuple[JudgeRecord, ...]:
    citation = f"artifact://blinded/{'c' * 64}"
    return tuple(
        JudgeRecord(
            candidate_id="candidate-a",
            view_sha256="d" * 64,
            panel_protocol_sha256="e" * 64,
            schedule_position=0,
            judge_slot=slot,
            model_id=f"judge-{slot}",
            model_family=f"family-{slot}",
            diversity_label="diverse",
            requires_stronger_calibration=False,
            runtime_lock_sha256="f" * 64,
            model_lock_sha256="0" * 64,
            rubric_sha256="1" * 64,
            system_prompt_sha256="2" * 64,
            tools_sha256="3" * 64,
            decoding_controls_sha256="4" * 64,
            status="completed",
            criteria={
                name: JudgeCriterionScore(score, 0.1, (citation,)) for name in JUDGE_CRITERIA
            },
        )
        for slot in (1, 2, 3)
    )


def _panel_gate(
    records: tuple[JudgeRecord, ...], *, reliability_passed: bool = True
) -> PanelGateEvidence:
    records_sha256 = sha256(canonical_bytes([record.document() for record in records])).hexdigest()
    status = "passed" if reliability_passed else "failed"
    decision = GateDecision(status, 1.0 if reliability_passed else 0.0, 0.7, "minimum")
    return PanelGateEvidence(
        qualification_protocol_sha256="5" * 64,
        panel_protocol_sha256=records[0].panel_protocol_sha256,
        records_sha256=records_sha256,
        diversity_label="diverse",
        criterion_agreement={name: CriterionAgreement("icc", decision) for name in JUDGE_CRITERIA},
        gates=dict.fromkeys(
            (
                "agreement",
                "human_calibration",
                "drift",
                "duplicate",
                "sentinel",
                "leave_one_out",
                "family_sensitivity",
                "shared_bias",
                "leakage",
            ),
            decision,
        ),
        per_judge_drift={},
        per_criterion_calibration_mae=dict.fromkeys(JUDGE_CRITERIA, 0.0),
        blocking_codes=(),
    )


def _adjudication(
    records: tuple[JudgeRecord, ...], *, status: str = "not_triggered"
) -> AdjudicationRecord:
    evaluations = {
        name: ThresholdEvaluation(name, 0.2, 0.0, False, "evaluated") for name in JUDGE_CRITERIA
    }
    return AdjudicationRecord(
        "candidate-a",
        records[0].view_sha256,
        records[0].panel_protocol_sha256,
        "6" * 64,
        tuple(record.sha256 for record in records),
        evaluations,
        status,
        failure_code=None if status == "completed" else "adjudication_not_triggered",
    )


def _aggregation() -> FrozenQualitativeAggregation:
    return FrozenQualitativeAggregation(
        dict.fromkeys(JUDGE_CRITERIA, 1 / len(JUDGE_CRITERIA)),
        pass_threshold=0.75,
    )


def test_favorable_quality_preserves_hard_failure_and_authority_conflict() -> None:
    records = _records()
    outcomes = normalize_outcomes(
        "candidate-a",
        _grader(GraderTerminalKind.FAILED),
        records,
        _panel_gate(records),
        _adjudication(records),
        _aggregation(),
    )

    assert outcomes.hard.status == "failed"
    assert outcomes.qualitative.status == "passed"
    assert outcomes.authority_conflict
    assert not outcomes.eligible_for_claims
    assert not outcomes.eligible_for_paid_or_study


def test_categorical_blocker_never_rewrites_endpoint_authority() -> None:
    records = _records()
    blocker = CategoricalBlockerRecord("security", "secret_scan_failed", ("7" * 64,))
    outcomes = normalize_outcomes(
        "candidate-a",
        _grader(),
        records,
        _panel_gate(records),
        _adjudication(records),
        _aggregation(),
        categorical_blockers=(blocker,),
    )

    assert outcomes.hard.status == "passed"
    assert outcomes.qualitative.status == "passed"
    assert outcomes.authority_conflict
    assert outcomes.categorical_blockers == (blocker,)
    assert not outcomes.eligible_for_claims


def test_failed_panel_gate_blocks_only_qualitative_finalization() -> None:
    records = _records()
    outcomes = normalize_outcomes(
        "candidate-a",
        _grader(),
        records,
        _panel_gate(records, reliability_passed=False),
        _adjudication(records),
        _aggregation(),
    )

    assert outcomes.hard.status == "passed"
    assert outcomes.qualitative.status == "blocked"
    assert outcomes.qualitative.value is None
    assert "panel_reliability_gate_failed_or_unavailable" in outcomes.normalization_codes


def test_missing_or_unauthorized_evidence_fails_closed_without_zero_substitution() -> None:
    records = _records()
    missing = normalize_outcomes("candidate-a", None, (), None, None, None)
    unauthorized = normalize_outcomes(
        "candidate-a",
        _grader(),
        records,
        _panel_gate(records),
        _adjudication(records),
        _aggregation(),
        artifact_authority="durable",
    )

    assert missing.hard.status == missing.qualitative.status == "unavailable"
    assert missing.hard.value is missing.qualitative.value is None
    assert unauthorized.hard.status == unauthorized.qualitative.status == "unavailable"
    assert "outcome_artifact_authority_unauthorized" in unauthorized.normalization_codes


def test_adjudication_failure_blocks_quality_but_cannot_change_hard_endpoint() -> None:
    records = _records()
    outcomes = normalize_outcomes(
        "candidate-a",
        _grader(),
        records,
        _panel_gate(records),
        _adjudication(records, status="failed"),
        _aggregation(),
    )

    assert outcomes.hard.status == "passed"
    assert outcomes.qualitative.status == "blocked"
    assert outcomes.qualitative.unavailable_reason == "adjudication_failed"


def test_endpoint_schema_and_derivation_hash_reject_tampering() -> None:
    records = _records()
    outcomes = normalize_outcomes(
        "candidate-a",
        _grader(),
        records,
        _panel_gate(records),
        _adjudication(records),
        _aggregation(),
    )
    schema_path = Path(__file__).parents[3] / "schemas" / "endpoint-record.schema.json"
    validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))

    validator.validate(outcomes.hard.document())
    validator.validate(outcomes.qualitative.document())
    with pytest.raises(JudgePanelConformanceError, match="endpoint derivation hash mismatch"):
        replace(outcomes.hard, derivation_sha256="0" * 64)
