from __future__ import annotations

import json
from dataclasses import replace

import pytest
from memrelay_eval.analysis.claims import ClaimScope, bound_claim, lint_claim_text
from memrelay_eval.analysis.gates import ClaimGateDecision, ReleaseFitnessDecision
from memrelay_eval.analysis.reports import (
    REPORT_TEMPLATE_SHA256,
    ReportInput,
    publish_report,
    render_report,
)
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.cli.main import main
from memrelay_eval.domain.errors import AnalysisError

_PROTOCOL = "a" * 64
_SOURCE = "b" * 64
_DERIVATION = "c" * 64
_FAMILY = "d" * 64
_POWER = "e" * 64
_ENVIRONMENT = "f" * 64
_EVIDENCE = "evidence-primary"


def _scope() -> ClaimScope:
    return ClaimScope(
        protocol_sha256=_PROTOCOL,
        population_id="primary-itt",
        model_id="model-primary",
        endpoint_id="EP-PRIM-SUCCESS",
        stratum="product",
        history_regime="controlled",
        environment_sha256=_ENVIRONMENT,
        source_sha256=(_SOURCE,),
        derivation_sha256=_DERIVATION,
        evidence_ids=(_EVIDENCE,),
    )


def _decision(status: str = "pass") -> ClaimGateDecision:
    return ClaimGateDecision(
        endpoint_id="EP-PRIM-SUCCESS",
        claim_type="reliability_benefit",
        claim_id="claim-primary",
        status=status,
        gate_trace=("frozen",),
        source_sha256=_SOURCE,
        derivation_sha256=_DERIVATION,
        protocol_sha256=_PROTOCOL,
        family_sha256=_FAMILY,
        sealed_claim_protocol_sha256="1" * 64,
        threshold_sha256="2" * 64,
        power_sha256=_POWER,
        power_evaluation_sha256="3" * 64,
        information_sha256="4" * 64,
        panel_gate_sha256=None,
        categorical_policy_sha256="5" * 64,
        categorical_gate_decision_sha256="6" * 64,
    )


def _release(decision: ClaimGateDecision, *, status: str = "pass") -> ReleaseFitnessDecision:
    return ReleaseFitnessDecision(
        status=status,
        family_sha256=_FAMILY,
        protocol_sha256=_PROTOCOL,
        population_id="primary-itt",
        model_id="model-primary",
        stratum="product",
        history_regime="controlled",
        environment_sha256=_ENVIRONMENT,
        source_sha256=(_SOURCE,),
        derivation_sha256=_DERIVATION,
        evidence_sha256=("7" * 64,),
        categorical_gate_decision_sha256=("6" * 64,),
        target_claim_decision_sha256=(decision.decision_sha256,),
        non_target_interval_sha256=("8" * 64,),
        reproduction_status="verified",
    )


def _sections(scope: ClaimScope) -> dict[str, tuple[dict[str, object], ...]]:
    return {
        name: (
            {
                "item_id": name,
                "source_sha256": list(scope.source_sha256),
                "derivation_sha256": scope.derivation_sha256,
                "evidence_ids": list(scope.evidence_ids),
                "protocol_sha256": scope.protocol_sha256,
                "population_id": scope.population_id,
                "model_id": scope.model_id,
                "endpoint_id": scope.endpoint_id,
                "stratum": scope.stratum,
                "history_regime": scope.history_regime,
                "environment_sha256": scope.environment_sha256,
                "value": "unavailable",
            },
        )
        for name in (
            "simultaneous_intervals",
            "marginal_descriptive_intervals",
            "diagnostics",
            "pareto_surfaces",
            "harm_tails",
            "safety",
            "costs",
            "time",
            "panel_metrics",
            "gates",
        )
    }


def _input(*, source_kind: str = "completed_reconciled_product") -> ReportInput:
    scope = _scope()
    decision = _decision()
    return ReportInput(
        report_id="primary-report",
        stage="primary",
        scope=scope,
        dataset_manifest_sha256="9" * 64,
        table_sha256=("a" * 64,),
        figure_sha256=("b" * 64,),
        estimator_sha256="c" * 64,
        interval_sha256=("d" * 64,),
        family_sha256=_FAMILY,
        power_sha256=_POWER,
        safety_sha256="e" * 64,
        panel_sha256="f" * 64,
        cost_revision_sha256="0" * 64,
        runtime_lock_sha256="a" * 64,
        template_sha256=REPORT_TEMPLATE_SHA256,
        gate_ids=("gate-primary",),
        claim_decisions=(decision,),
        release_fitness=_release(decision),
        source_kind=source_kind,
        reproduction_status="verified",
        sections=_sections(scope),
    )


def test_report_is_deterministic_and_binds_every_required_surface(tmp_path) -> None:
    report = render_report(_input())
    first = publish_report(report, tmp_path)
    second = publish_report(render_report(_input()), tmp_path)

    document = json.loads((first / "report.json").read_text(encoding="utf-8"))
    assert first == second
    assert document["terminal_status"] == "verified"
    assert set(document["sections"]) == set(_sections(_scope()))
    assert document["claims"][0]["terminal_status"] == "positive"
    assert document["claims"][0]["scope"]["model_id"] == "model-primary"


@pytest.mark.parametrize(
    ("source_kind", "reproduction_status"),
    (
        ("construction", "verified"),
        ("component_test", "verified"),
        ("deterministic_fixture", "verified"),
        ("unreconciled_trial", "verified"),
        ("engine_upper_bound", "verified"),
        ("pilot", "verified"),
        ("completed_reconciled_product", "pending"),
    ),
)
def test_nonconfirmatory_sources_cannot_be_promoted(
    source_kind: str, reproduction_status: str
) -> None:
    claim = bound_claim(
        _decision(),
        _scope(),
        source_kind=source_kind,
        reproduction_status=reproduction_status,
    )

    assert claim.terminal_status == "indeterminate"
    assert "release-fitness conclusion" in claim.language


def test_harmful_and_null_language_follow_existing_decision_authority() -> None:
    no_regression = replace(_decision("fail"), claim_type="no_regression", claim_id="claim-harm")
    scope = _scope()
    harmful = bound_claim(
        no_regression,
        scope,
        source_kind="completed_reconciled_product",
        reproduction_status="verified",
    )

    assert harmful.terminal_status == "harmful"
    with pytest.raises(AnalysisError, match="language forbidden"):
        lint_claim_text("The product is safe.")


def test_report_rejects_item_without_complete_scope_lineage() -> None:
    report_input = _input()
    bad_sections = _sections(_scope())
    bad_sections["costs"][0]["evidence_ids"] = []

    with pytest.raises(AnalysisError, match="item lineage"):
        replace(report_input, sections=bad_sections)


def test_report_rejects_unreconciled_trial_input() -> None:
    with pytest.raises(AnalysisError, match="unreconciled input"):
        _input(source_kind="unreconciled_trial")


def test_cli_renders_only_canonical_explicit_report_input(tmp_path, capsys) -> None:
    report_input = _input()
    path = tmp_path / "report-input.json"
    path.write_bytes(canonical_bytes(report_input.to_document()))

    assert (
        main(
            (
                "report",
                "--stage",
                "primary",
                "--input",
                str(path),
                "--output-root",
                str(tmp_path / "artifacts"),
            )
        )
        == 0
    )
    assert "report_sha256" in capsys.readouterr().out
