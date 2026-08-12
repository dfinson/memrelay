from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest
from memrelay_eval.analysis.claims import ReleaseClaimScope
from memrelay_eval.domain.errors import AnalysisError
from memrelay_eval.evidence.release_map import (
    ReleaseEvidence,
    ReleaseMapPolicy,
    ReleaseStatement,
    map_release_evidence,
)


def _scope(
    *,
    evidence_id: str = "EV-FIXTURE-RETRIEVAL",
    gate_id: str = "GATE-FIXTURE",
) -> ReleaseClaimScope:
    return ReleaseClaimScope(
        artifact_id="artifact-opaque-1",
        artifact_sha256="8" * 64,
        evidence_id=evidence_id,
        path="release-fixture",
        observation_mode="not_applicable",
        configuration_sha256="a" * 64,
        source_implementation_sha256="b" * 64,
        runtime_lock_sha256="c" * 64,
        version_sha256="d" * 64,
        protocol_sha256="e" * 64,
        population_id="synthetic-fixture",
        model_id="deterministic-double",
        stratum="product",
        history_regime="fixture",
        endpoint_id="EP-WIRING",
        estimand_id="deterministic-wiring",
        source_sha256=("f" * 64,),
        derivation_sha256="0" * 64,
        reconciliation_sha256="1" * 64,
        gate_id=gate_id,
        gate_sha256="2" * 64,
    )


def _fixture(
    *,
    terminal_status: str = "passed",
    gate_status: str = "passed",
) -> ReleaseEvidence:
    return ReleaseEvidence(
        _scope(),
        "fixture_retrieval",
        "CL-WIRING-RANK",
        ("production efficacy",),
        terminal_status,
        gate_status,
    )


def _statement(
    *,
    scope: ReleaseClaimScope | None = None,
    statement_kind: str = "bounded_product_regression",
    statement: str = "CL-WIRING-RANK",
) -> ReleaseStatement:
    return ReleaseStatement("statement-1", statement_kind, scope or _scope(), statement)


@pytest.mark.parametrize(
    ("terminal_status", "expected"),
    (
        ("passed", "positive"),
        ("null", "null"),
        ("harmful", "harmful"),
        ("indeterminate", "indeterminate"),
    ),
)
def test_fixture_terminal_statuses_are_typed(terminal_status: str, expected: str) -> None:
    result = map_release_evidence((_fixture(terminal_status=terminal_status),), (_statement(),))

    assert result.decisions[0].terminal_status == expected
    assert result.decisions[0].supports == "Supports only CL-WIRING-RANK."
    assert "Does not support" in result.decisions[0].does_not_support


@pytest.mark.parametrize(
    "terminal_status", ("expired", "drifted", "conflicting", "missing", "blocked")
)
def test_expired_or_conflicting_evidence_fails_closed(terminal_status: str) -> None:
    result = map_release_evidence((_fixture(terminal_status=terminal_status),), (_statement(),))

    assert result.decisions[0].terminal_status == "blocked"


def test_observation_evidence_is_estimation_only() -> None:
    scope = _scope(evidence_id="RELEASE-CONTINUOUS", gate_id="GATE-OBSERVATION")
    evidence = ReleaseEvidence(
        scope,
        "observation_sentinel",
        "OBSERVATION-REPLAY-DELIVERY",
        ("causal treatment effect",),
        "passed",
        "passed",
    )
    statement = _statement(
        scope=scope,
        statement_kind="observation_qualification",
        statement="OBSERVATION-REPLAY-DELIVERY",
    )

    result = map_release_evidence((evidence,), (statement,))

    assert result.decisions[0].terminal_status == "estimation-only"


def test_missing_evidence_is_blocked_except_explicit_unqualified_policy() -> None:
    statement = _statement()

    blocked = map_release_evidence((), (statement,))
    unqualified = map_release_evidence(
        (), (statement,), policy=ReleaseMapPolicy(allow_unqualified=True)
    )

    assert blocked.decisions[0].terminal_status == "blocked"
    assert unqualified.decisions[0].terminal_status == "unqualified"


def test_failed_gate_and_fixture_efficacy_promotion_are_blocked() -> None:
    gate_failed = map_release_evidence((_fixture(gate_status="failed"),), (_statement(),))
    promoted = map_release_evidence(
        (_fixture(),),
        (
            _statement(
                statement_kind="confirmatory_shipped_product_efficacy",
                statement="CL-WIRING-RANK",
            ),
        ),
    )

    assert gate_failed.decisions[0].terminal_status == "blocked"
    assert promoted.decisions[0].terminal_status == "blocked"
    assert "nonconfirmatory_evidence_cannot_promote_efficacy" in promoted.decisions[0].reasons


def test_fixture_identities_remain_separate() -> None:
    scope = _scope(evidence_id="EV-ROUNDTRIP-MCP")

    with pytest.raises(AnalysisError, match="fixture"):
        ReleaseEvidence(
            scope,
            "fixture_roundtrip",
            "CL-WIRING-RANK",
            ("efficacy",),
            "passed",
            "passed",
        )


def test_map_is_immutable_and_scope_conflicts_cannot_substitute() -> None:
    evidence = _fixture()
    statement = _statement(scope=replace(_scope(), configuration_sha256="9" * 64))

    result = map_release_evidence((evidence,), (statement,))

    assert result.decisions[0].terminal_status == "blocked"
    assert result.decisions[0].reasons == ("configuration_sha256_conflict",)


def test_release_map_satisfies_its_contract_schema() -> None:
    result = map_release_evidence((_fixture(),), (_statement(),))
    schema = json.loads(
        (Path(__file__).parents[3] / "schemas" / "release-evidence-map.schema.json").read_text(
            "utf-8"
        )
    )

    jsonschema.Draft202012Validator(schema).validate(result.to_document())
