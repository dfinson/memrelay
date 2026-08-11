from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from memrelay_eval.domain.states import AttemptTerminalKind, EvaluationStratum, HistoryMode
from memrelay_eval.evidence.required import (
    REQUIRED_EVIDENCE_PRODUCER_POLICY,
    EvidenceKind,
    EvidenceMatrixKey,
    RequirementMode,
    all_terminal_matrix_keys,
    required_evidence_matrix,
)


def test_every_terminal_condition_selects_a_complete_frozen_matrix() -> None:
    matrices = [required_evidence_matrix(key) for key in all_terminal_matrix_keys()]

    assert len(matrices) == 1920
    for matrix in matrices:
        assert set(matrix.requirements) == set(EvidenceKind)
        assert matrix.sha256 == required_evidence_matrix(matrix.key).sha256
        assert matrix.requirements[EvidenceKind.ASSIGNMENT].mode is RequirementMode.PRIMARY_REQUIRED
        assert matrix.requirements[EvidenceKind.TELEMETRY].mode is RequirementMode.PRIMARY_REQUIRED
        assert matrix.requirements[EvidenceKind.COST_COPILOT].mode is (
            RequirementMode.EXPLICIT_UNAVAILABLE_PERMITTED
        )
        assert matrix.requirements[EvidenceKind.REFERENCED_HASHES].mode is (
            RequirementMode.PRIMARY_REQUIRED
        )


def test_frozen_producer_policy_covers_each_matrix_authority() -> None:
    authorities = {
        authority
        for matrix_key in all_terminal_matrix_keys()
        for requirement in required_evidence_matrix(matrix_key).requirements.values()
        for authority in requirement.authorities
    }

    assert authorities == set(REQUIRED_EVIDENCE_PRODUCER_POLICY)
    assert all(
        identities and all(identity.component and identity.version for identity in identities)
        for identities in REQUIRED_EVIDENCE_PRODUCER_POLICY.values()
    )


def test_panel_and_adjudication_requirements_follow_the_frozen_condition_key() -> None:
    base = {
        "stage": "pilot",
        "stratum": EvaluationStratum.PRODUCT,
        "history_mode": HistoryMode.CONTROLLED,
        "task_state": "terminal",
        "failure_state": AttemptTerminalKind.SUCCEEDED,
        "provider_path": "copilot_task_agent",
    }
    no_panel = required_evidence_matrix(
        EvidenceMatrixKey(
            **base, grader_required=True, panel_required=False, adjudication_required=False
        )
    )
    conditional_adjudication = required_evidence_matrix(
        EvidenceMatrixKey(
            **base, grader_required=True, panel_required=True, adjudication_required=False
        )
    )
    required_adjudication = required_evidence_matrix(
        EvidenceMatrixKey(
            **base, grader_required=True, panel_required=True, adjudication_required=True
        )
    )

    assert no_panel.requirements[EvidenceKind.PANEL].mode is RequirementMode.PROHIBITED
    assert conditional_adjudication.requirements[EvidenceKind.ADJUDICATION].mode is (
        RequirementMode.CONDITIONALLY_REQUIRED
    )
    assert required_adjudication.requirements[EvidenceKind.ADJUDICATION].mode is (
        RequirementMode.PRIMARY_REQUIRED
    )


def test_matrix_modes_follow_stage_history_provider_and_pre_exposure_terminal() -> None:
    pre_exposure = required_evidence_matrix(
        EvidenceMatrixKey(
            "integration",
            EvaluationStratum.PRODUCT,
            HistoryMode.CONTROLLED,
            "terminal",
            AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
            "copilot_task_agent",
            True,
            True,
            True,
        )
    )
    direct_dynamic = required_evidence_matrix(
        EvidenceMatrixKey(
            "primary",
            EvaluationStratum.DIRECT_ENGINE,
            HistoryMode.DYNAMIC,
            "terminal",
            AttemptTerminalKind.SUCCEEDED,
            "direct_engine",
            True,
            False,
            False,
        )
    )
    conformance_panel = required_evidence_matrix(
        EvidenceMatrixKey(
            "conformance",
            EvaluationStratum.PRODUCT,
            HistoryMode.CONTROLLED,
            "terminal",
            AttemptTerminalKind.SUCCEEDED,
            "copilot_task_agent",
            True,
            True,
            True,
        )
    )

    assert (
        pre_exposure.requirements[EvidenceKind.LIFECYCLE].mode is RequirementMode.PRIMARY_REQUIRED
    )
    assert pre_exposure.requirements[EvidenceKind.INSPECT_EVAL].mode is RequirementMode.PROHIBITED
    assert pre_exposure.requirements[EvidenceKind.SDK_TERMINAL].mode is RequirementMode.PROHIBITED
    assert pre_exposure.requirements[EvidenceKind.TREATMENT].mode is RequirementMode.PROHIBITED
    assert pre_exposure.requirements[EvidenceKind.WORKSPACE_BASELINE].mode is (
        RequirementMode.EXPLICIT_UNAVAILABLE_PERMITTED
    )
    assert direct_dynamic.requirements[EvidenceKind.SDK_EVENTS].mode is RequirementMode.PROHIBITED
    assert direct_dynamic.requirements[EvidenceKind.INSPECT_JSON].mode is (
        RequirementMode.PRIMARY_REQUIRED
    )
    assert direct_dynamic.requirements[EvidenceKind.WORKSPACE_BASELINE].mode is (
        RequirementMode.EXPLICIT_UNAVAILABLE_PERMITTED
    )
    assert direct_dynamic.requirements[EvidenceKind.TREATMENT].authorities == (
        "direct_engine_treatment",
    )
    assert conformance_panel.requirements[EvidenceKind.PANEL].mode is RequirementMode.PROHIBITED
    assert conformance_panel.requirements[EvidenceKind.ADJUDICATION].mode is (
        RequirementMode.PROHIBITED
    )


def test_required_matrix_schema_accepts_the_canonical_projection() -> None:
    schema_path = Path(__file__).parents[3] / "schemas" / "required-evidence.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    matrix = required_evidence_matrix(
        EvidenceMatrixKey(
            "integration",
            EvaluationStratum.DIRECT_ENGINE,
            HistoryMode.DYNAMIC,
            "terminal",
            AttemptTerminalKind.TIMED_OUT,
            "direct_engine",
            True,
            True,
            False,
        )
    )

    Draft202012Validator(schema).validate(matrix.to_document())


def test_matrix_schemas_reject_a_repeated_kind_matrix() -> None:
    matrix = required_evidence_matrix(
        EvidenceMatrixKey(
            "integration",
            EvaluationStratum.PRODUCT,
            HistoryMode.CONTROLLED,
            "terminal",
            AttemptTerminalKind.SUCCEEDED,
            "copilot_task_agent",
            True,
            False,
            False,
        )
    ).to_document()
    repeated = dict(matrix)
    repeated["requirements"] = [dict(matrix["requirements"][0]) for _ in EvidenceKind]
    schemas = Path(__file__).parents[3] / "schemas"
    standalone = Draft202012Validator(
        json.loads((schemas / "required-evidence.schema.json").read_text(encoding="utf-8"))
    )
    report = Draft202012Validator(
        json.loads((schemas / "reconciliation-report.schema.json").read_text(encoding="utf-8"))
    )
    report_document = {
        "schema_version": "1.0.0",
        "artifact_type": "reconciliation_report",
        "input": {},
        "matrix": repeated,
        "matrix_sha256": "a" * 64,
        "primary_required": 0,
        "primary_present": 0,
        "primary_complete": False,
        "blockers": [],
        "conflicts": [],
        "telemetry_transport_only": True,
        "reconciliation_sha256": "b" * 64,
    }

    assert list(standalone.iter_errors(repeated))
    assert list(report.iter_errors(report_document))


def test_inclusion_decision_schema_rejects_nonterminal_or_noncanonical_values() -> None:
    schema_path = Path(__file__).parents[3] / "schemas" / "inclusion-decision.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    document = {
        "schema_version": "1.0.0",
        "inclusion_id": "inclusion_" + "a" * 32,
        "run_id": "run_" + "b" * 32,
        "status": "excluded",
        "reason_code": "missing_primary_evidence",
        "reconciliation_sha256": "c" * 64,
        "occurred_at": "2026-08-10T12:00:00Z",
    }

    validator.validate(document)
    document["status"] = "pending"
    assert list(validator.iter_errors(document))
