from __future__ import annotations

import pytest
from memrelay_eval.analysis.estimands import FrozenEstimatorRegistry
from memrelay_eval.domain.errors import AnalysisError
from tests.unit.analysis.test_itt_outcomes import _DATASET, _PROTOCOL, _estimand


def test_registry_is_deterministic_and_rejects_drift_or_multiplicity_leakage() -> None:
    first = FrozenEstimatorRegistry((_estimand(),))
    second = FrozenEstimatorRegistry((_estimand(),))

    assert first.registry_sha256 == second.registry_sha256
    assert (
        first.require(
            "quality-itt",
            "1.0.0",
            protocol_sha256=_PROTOCOL,
            source_dataset_manifest_sha256=_DATASET,
        ).fingerprint
        == _estimand().fingerprint
    )
    with pytest.raises(AnalysisError, match="estimator source dataset drift"):
        first.require(
            "quality-itt",
            "1.0.0",
            protocol_sha256=_PROTOCOL,
            source_dataset_manifest_sha256="c" * 64,
        )
    with pytest.raises(AnalysisError, match="multiplicity leakage"):
        _estimand(multiplicity_owner="analysis")
