from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest
from memrelay_eval.analysis.claims import ClaimScope, lint_claim_text
from memrelay_eval.analysis.gates import CategoricalGateDecision
from memrelay_eval.analysis.intervals import SimultaneousInterval
from memrelay_eval.analysis.reports import (
    REPORT_TEMPLATE_SHA256,
    ReportInput,
    ReportItem,
    SourceAuthority,
    StageScope,
    publish_report,
    render_report,
)
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.cli.commands import _canonical_report_input
from memrelay_eval.cli.main import main
from memrelay_eval.domain.errors import AnalysisError
from tests.contract.analysis.test_duckdb_read_only import _dataset
from tests.unit.analysis.test_multiplicity_gates_power import (
    _DERIVATION,
    _ENVIRONMENT,
    _SOURCE,
    _bundle,
    _context,
    _holm,
    _interval,
)


def _scopes() -> tuple[StageScope, tuple[ClaimScope, ...]]:
    stage = StageScope(
        protocol_sha256="a" * 64,
        population_id="primary",
        model_id="model-primary",
        stratum="product",
        history_regime="controlled",
        environment_sha256=_ENVIRONMENT,
        source_sha256=(_SOURCE,),
        derivation_sha256=_DERIVATION,
        evidence_ids=("evidence-primary",),
    )
    return stage, tuple(
        ClaimScope(
            protocol_sha256=stage.protocol_sha256,
            population_id=stage.population_id,
            model_id=stage.model_id,
            endpoint_id=endpoint,
            stratum=stage.stratum,
            history_regime=stage.history_regime,
            environment_sha256=stage.environment_sha256,
            source_sha256=stage.source_sha256,
            derivation_sha256=stage.derivation_sha256,
            evidence_ids=stage.evidence_ids,
        )
        for endpoint in ("EP-PRIM-SUCCESS", "EP-QUAL", "EP-HARM")
    )


def _item(name: str, scope: ClaimScope) -> ReportItem:
    return ReportItem(name, scope, {"authority": "retained"})


def _source_authority(
    source_kind: str,
    dataset_manifest_sha256: str,
    protocol_sha256: str,
    source_sha256: tuple[str, ...],
) -> SourceAuthority:
    basis = {
        "schema_version": "1.0.0",
        "artifact_type": "report_source_authority",
        "source_kind": source_kind,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "protocol_sha256": protocol_sha256,
        "source_sha256": list(source_sha256),
    }
    return SourceAuthority(
        source_kind=source_kind,
        dataset_manifest_sha256=dataset_manifest_sha256,
        protocol_sha256=protocol_sha256,
        source_sha256=source_sha256,
        authority_sha256=canonical_digest(basis),
    )


def _input(
    *, failing_claim: bool = False, source_kind: str = "completed_reconciled_product"
) -> ReportInput:
    bundle = _bundle()
    context = _context(bundle)
    categorical = context["categorical_gate_decision"]
    assert isinstance(categorical, CategoricalGateDecision)
    decisions = (
        replace(
            __import__("memrelay_eval.analysis.gates", fromlist=["evaluate_claim"]).evaluate_claim(
                bundle.family,
                bundle.thresholds,
                _holm(bundle.family),
                _interval(bundle.family),
                claim_type="reliability_benefit",
                **context,
            ),
            status="fail" if failing_claim else "pass",
        ),
    )
    stage, scopes = _scopes()
    sections = {
        name: (_item(name, scopes[index % len(scopes)]),)
        for index, name in enumerate(
            (
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
        )
    }
    return ReportInput(
        report_id="primary-report",
        stage="primary",
        scope=stage,
        dataset_manifest_sha256="9" * 64,
        table_sha256=("a" * 64,),
        figure_sha256=("b" * 64,),
        estimator_sha256="c" * 64,
        interval_sha256=("d" * 64,),
        power_sha256=bundle.protocol.power_sha256,
        safety_sha256="e" * 64,
        panel_sha256="f" * 64,
        cost_revision_sha256="0" * 64,
        runtime_lock_sha256="1" * 64,
        template_sha256=REPORT_TEMPLATE_SHA256,
        gate_ids=("gate-primary",),
        family=bundle.family,
        claim_decisions=decisions,
        claim_scopes=(scopes[0],),
        non_target_intervals=(
            _interval(bundle.family, "EP-QUAL", point=0.06, lower=0.001),
            SimultaneousInterval(
                "EP-HARM",
                0.0,
                -0.019,
                0.01,
                0.95,
                "holm-compatible",
                bundle.family.family_sha256,
                sidedness="two-sided",
            ),
        ),
        categorical_policy=bundle.categorical_policy,
        categorical_decisions=(categorical,),
        source_authority=_source_authority(
            source_kind, "9" * 64, stage.protocol_sha256, stage.source_sha256
        ),
        reproduction_status="verified",
        sections=sections,
    )


def test_report_recomputes_fitness_and_supports_multi_endpoint_sections(tmp_path: Path) -> None:
    report = render_report(_input())
    directory = publish_report(report, tmp_path)
    document = json.loads((directory / "report.json").read_text("utf-8"))
    schema = json.loads(
        (Path(__file__).parents[3] / "schemas" / "evidence-linked-report.schema.json").read_text(
            "utf-8"
        )
    )
    input_schema = json.loads(
        (Path(__file__).parents[3] / "schemas" / "frozen-report-input.schema.json").read_text(
            "utf-8"
        )
    )
    claim_schema = json.loads(
        (Path(__file__).parents[3] / "schemas" / "bounded-claim.schema.json").read_text("utf-8")
    )

    jsonschema.Draft202012Validator(schema).validate(document)
    jsonschema.Draft202012Validator(input_schema).validate(report.report_input.to_document())
    for claim in report.claims:
        jsonschema.Draft202012Validator(claim_schema).validate(claim.to_document())
    assert document["terminal_status"] == "verified"
    assert {
        item["scope"]["endpoint_id"] for items in document["sections"].values() for item in items
    } == {
        "EP-PRIM-SUCCESS",
        "EP-QUAL",
        "EP-HARM",
    }


def test_failing_claim_cannot_render_release_pass() -> None:
    report_input = _input(failing_claim=True)

    assert report_input.release_fitness.status == "fail"
    assert render_report(report_input).terminal_status == "draft/unverified"


def test_incomplete_or_duplicate_categorical_scope_fails_closed() -> None:
    report_input = _input()
    missing = replace(report_input, categorical_decisions=())
    with pytest.raises(AnalysisError, match="authority missing"):
        _ = missing.release_fitness
    duplicate = replace(
        report_input,
        categorical_decisions=(
            report_input.categorical_decisions[0],
            report_input.categorical_decisions[0],
        ),
    )
    with pytest.raises(AnalysisError, match="scope incomplete"):
        _ = duplicate.release_fitness


def test_claim_lint_rejects_broad_safety_language() -> None:
    with pytest.raises(AnalysisError, match="language forbidden"):
        lint_claim_text("The product is safe.")


@pytest.mark.parametrize(
    "source_kind",
    (
        "construction",
        "component_test",
        "deterministic_fixture",
        "unreconciled_trial",
        "engine_upper_bound",
        "pilot",
        "completed_reconciled_product",
    ),
)
def test_source_authority_controls_claim_promotion(source_kind: str) -> None:
    report = render_report(_input(source_kind=source_kind))

    if source_kind == "completed_reconciled_product":
        assert report.claims[0].terminal_status == "positive"
        assert report.terminal_status == "verified"
    else:
        assert report.claims[0].terminal_status == "indeterminate"
        assert report.terminal_status == "draft/unverified"


def test_tampered_source_authority_fails_closed() -> None:
    report_input = _input()
    with pytest.raises(AnalysisError, match="source authority"):
        replace(
            report_input,
            source_authority=replace(
                report_input.source_authority, source_kind="engine_upper_bound"
            ),
        )


def test_sealed_input_rejects_caller_authored_release_pass() -> None:
    document = _input(failing_claim=True).to_document()
    document["release_fitness"]["status"] = "pass"

    with pytest.raises(AnalysisError, match="authority conflict"):
        _canonical_report_input(canonical_bytes(document))


def test_cli_renders_from_verified_completed_parquet_stage(tmp_path: Path, capsys) -> None:
    dataset = _dataset(tmp_path)
    report_input = _input()
    protocol = dataset.manifest["protocol_sha256"][0]
    source_manifest_sha256 = tuple(dataset.manifest["source_manifest_sha256"])
    family = replace(
        report_input.family,
        protocol_sha256=protocol,
        source_dataset_manifest_sha256=dataset.manifest_sha256,
    )
    decisions = tuple(
        replace(
            decision,
            protocol_sha256=protocol,
            family_sha256=family.family_sha256,
            source_sha256=source_manifest_sha256[0],
        )
        for decision in report_input.claim_decisions
    )
    intervals = tuple(
        replace(interval, family_sha256=family.family_sha256)
        for interval in report_input.non_target_intervals
    )
    stage_scope = replace(
        report_input.scope,
        protocol_sha256=protocol,
        population_id=dataset.manifest["population_id"][0],
        stratum=dataset.manifest["stratum"][0],
        history_regime=dataset.manifest["history_mode"][0],
        source_sha256=source_manifest_sha256,
    )
    claim_scopes = tuple(
        replace(
            scope,
            protocol_sha256=protocol,
            population_id=stage_scope.population_id,
            stratum=stage_scope.stratum,
            history_regime=stage_scope.history_regime,
            source_sha256=stage_scope.source_sha256,
        )
        for scope in report_input.claim_scopes
    )
    sections = {
        name: tuple(
            replace(
                item,
                scope=replace(
                    item.scope,
                    protocol_sha256=stage_scope.protocol_sha256,
                    population_id=stage_scope.population_id,
                    model_id=stage_scope.model_id,
                    stratum=stage_scope.stratum,
                    history_regime=stage_scope.history_regime,
                    environment_sha256=stage_scope.environment_sha256,
                    source_sha256=stage_scope.source_sha256,
                    derivation_sha256=stage_scope.derivation_sha256,
                    evidence_ids=stage_scope.evidence_ids,
                ),
            )
            for item in items
        )
        for name, items in report_input.sections.items()
    }
    stage_input = replace(
        report_input,
        scope=stage_scope,
        dataset_manifest_sha256=dataset.manifest_sha256,
        family=family,
        claim_decisions=decisions,
        claim_scopes=claim_scopes,
        non_target_intervals=intervals,
        sections=sections,
        source_authority=_source_authority(
            "completed_reconciled_product",
            dataset.manifest_sha256,
            protocol,
            stage_scope.source_sha256,
        ),
    )
    evidence = tmp_path / "stage-evidence.json"
    evidence.write_bytes(canonical_bytes(stage_input.to_document()))

    assert (
        main(
            (
                "report",
                "--stage",
                "primary",
                "--stage-evidence",
                str(evidence),
                "--parquet-root",
                str(dataset.root),
                "--dataset-version",
                dataset.dataset_version,
                "--output-root",
                str(tmp_path / "artifacts"),
            )
        )
        == 0
    )
    assert "report_sha256" in capsys.readouterr().out
