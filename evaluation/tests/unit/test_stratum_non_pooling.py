from __future__ import annotations

import asyncio

import pytest
from memrelay_eval.domain.engine import (
    FrameworkConfiguration,
    StratifiedOperation,
    StratumAuthority,
    require_distinct_stratum_authorities,
    require_framework_configuration_parity,
    require_stratified_operation,
)
from memrelay_eval.domain.errors import DirectEngineBoundaryError, StratumPoolingError
from memrelay_eval.domain.ids import (
    AnalysisId,
    AssignmentId,
    ClaimId,
    CostEntryId,
    EndpointId,
    ProtocolId,
    ReportId,
    RunId,
    RuntimeId,
)
from memrelay_eval.domain.states import EvaluationStratum
from memrelay_eval.orchestration.attempt import DirectEngineAttemptController


def _authority(stratum: EvaluationStratum) -> StratumAuthority:
    engine = stratum is EvaluationStratum.DIRECT_ENGINE
    return StratumAuthority(
        stratum,
        ProtocolId.new(),
        AssignmentId.new(),
        RunId.new(),
        RuntimeId.new(),
        EndpointId.new(),
        CostEntryId.new(),
        AnalysisId.new(),
        ReportId.new(),
        ClaimId.new(),
        "mechanism_upper_bound" if engine else "product_efficacy",
        "engine upper bound" if engine else "product efficacy",
    )


@pytest.mark.parametrize("operation_name", ("join", "export", "estimator", "report"))
def test_cross_stratum_operations_require_explicit_stratification(operation_name: str) -> None:
    del operation_name
    product = _authority(EvaluationStratum.PRODUCT)
    engine = _authority(EvaluationStratum.DIRECT_ENGINE)
    require_distinct_stratum_authorities(product, engine)

    with pytest.raises(StratumPoolingError):
        require_stratified_operation((product, engine))

    assert require_stratified_operation((product, engine), StratifiedOperation.EXPLICIT) == (
        EvaluationStratum.PRODUCT,
        EvaluationStratum.DIRECT_ENGINE,
    )


def test_every_cross_stratum_identity_must_be_distinct() -> None:
    product = _authority(EvaluationStratum.PRODUCT)
    engine = _authority(EvaluationStratum.DIRECT_ENGINE)
    reused = StratumAuthority(
        engine.stratum,
        product.protocol_id,
        engine.assignment_id,
        engine.run_id,
        engine.runtime_id,
        engine.endpoint_id,
        engine.cost_entry_id,
        engine.analysis_id,
        engine.report_id,
        engine.claim_id,
        engine.claim_kind,
        engine.claim_label,
    )

    with pytest.raises(DirectEngineBoundaryError, match="cross stratum identity reuse"):
        require_distinct_stratum_authorities(product, reused)


def test_engine_claim_and_framework_parity_are_frozen() -> None:
    framework = FrameworkConfiguration()
    assert require_framework_configuration_parity(framework, FrameworkConfiguration()) == (
        framework.digest
    )
    with pytest.raises(DirectEngineBoundaryError, match="engine claim language invalid"):
        StratumAuthority(
            EvaluationStratum.DIRECT_ENGINE,
            ProtocolId.new(),
            AssignmentId.new(),
            RunId.new(),
            RuntimeId.new(),
            EndpointId.new(),
            CostEntryId.new(),
            AnalysisId.new(),
            ReportId.new(),
            ClaimId.new(),
            "product_efficacy",
            "product efficacy",
        )


def test_orchestration_gates_equal_framework_configuration_and_distinct_identities() -> None:
    product = _authority(EvaluationStratum.PRODUCT)
    engine = _authority(EvaluationStratum.DIRECT_ENGINE)
    calls: list[object] = []

    class Treatment:
        async def execute(self, attempt: object) -> object:
            calls.append(attempt)
            return "evidence"

    controller = DirectEngineAttemptController(
        Treatment(),
        product_authority=product,
        product_framework=FrameworkConfiguration(),
    )

    result = asyncio.run(
        controller.execute(
            "opaque-attempt",
            engine_authority=engine,
            engine_framework=FrameworkConfiguration(),
        )
    )

    assert result == "evidence"
    assert calls == ["opaque-attempt"]
    assert product.to_document()["protocol_id"] != engine.to_document()["protocol_id"]
