from __future__ import annotations

from dataclasses import replace

import pytest
from memrelay_eval.evidence.release_map import ReleaseEvidence, map_release_evidence

from tests.unit.evidence.test_release_evidence_map import _fixture, _scope, _statement


@pytest.mark.parametrize(
    "field",
    (
        "observation_mode",
        "version_sha256",
        "configuration_sha256",
        "stratum",
        "history_regime",
        "model_id",
        "population_id",
        "source_sha256",
        "derivation_sha256",
    ),
)
def test_cross_scope_substitution_fails_closed(field: str) -> None:
    scope = _scope()
    value = (
        "engine"
        if field == "stratum"
        else "other"
        if field
        not in {
            "version_sha256",
            "configuration_sha256",
            "derivation_sha256",
            "source_sha256",
        }
        else (("9" * 64,) if field == "source_sha256" else "9" * 64)
    )
    statement = _statement(scope=replace(scope, **{field: value}))

    result = map_release_evidence((_fixture(),), (statement,))

    assert result.decisions[0].terminal_status == "blocked"
    assert f"{field}_conflict" in result.decisions[0].reasons


def test_engine_and_observational_evidence_cannot_be_randomized_product_efficacy() -> None:
    engine_scope = replace(_scope(evidence_id="EV-ENGINE-ONLY"), stratum="engine")
    engine = ReleaseEvidence(
        engine_scope,
        "engine_upper_bound",
        "CL-ENGINE-ONLY",
        ("product efficacy",),
        "passed",
        "passed",
    )
    statement = _statement(
        scope=engine_scope,
        statement_kind="randomized_treatment_estimand",
        statement="CL-ENGINE-ONLY",
    )

    result = map_release_evidence((engine,), (statement,))

    assert result.decisions[0].terminal_status == "blocked"
    assert "observational_randomized_confusion" in result.decisions[0].reasons

    observation_scope = _scope(evidence_id="RELEASE-CONTINUOUS")
    observation = ReleaseEvidence(
        observation_scope,
        "observation_sentinel",
        "OBSERVATION-DELIVERY",
        ("causal treatment effect",),
        "passed",
        "passed",
    )
    observational_as_randomized = _statement(
        scope=observation_scope,
        statement_kind="randomized_treatment_estimand",
        statement="OBSERVATION-DELIVERY",
    )

    observation_result = map_release_evidence((observation,), (observational_as_randomized,))

    assert observation_result.decisions[0].terminal_status == "blocked"
    assert "observational_randomized_confusion" in observation_result.decisions[0].reasons
