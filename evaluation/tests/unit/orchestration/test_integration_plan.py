from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, ScenarioId, StageAuthorizationId, StageId
from memrelay_eval.domain.policies import STAGE_ENTRY_LOCK_FIELDS
from memrelay_eval.domain.states import StageKind, StageState
from memrelay_eval.orchestration.integration import (
    INTEGRATION_RUN_COUNT,
    INTEGRATION_SCENARIO_COUNT,
    authorize_integration_plan,
    load_integration_plan,
    seal_integration_plan,
)
from memrelay_eval.orchestration.limits import IntegrationStageLimits
from memrelay_eval.orchestration.stages import (
    StageAuthorization,
    StageEntryBundle,
    StageExitBundle,
)

HASH = "a" * 64
NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def _scenarios() -> tuple[ScenarioId, ...]:
    return tuple(
        ScenarioId.from_digest(canonical_digest({"scenario": index}))
        for index in range(INTEGRATION_SCENARIO_COUNT)
    )


def _plan():
    conformance_exit = StageExitBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.CONFORMANCE,
        protocol_id=ProtocolId.new(),
        entry_bundle_sha256=HASH,
        preceding_exit_sha256="b" * 64,
        status=StageState.ACCEPTED,
        reconciliation_sha256="c" * 64,
        inclusion_decision_sha256="d" * 64,
        authorization_id=StageAuthorizationId.new(),
    )
    limits = IntegrationStageLimits(ai_credit_cap=100.0, usd_cap=10.0, per_run_tool_call_cap=60)
    locks = dict.fromkeys(STAGE_ENTRY_LOCK_FIELDS, HASH)
    locks["preceding_exit_sha256"] = conformance_exit.digest
    locks["limits_sha256"] = limits.digest
    entry = StageEntryBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.INTEGRATION,
        protocol_id=ProtocolId.new(),
        predecessor_stage_kind=StageKind.CONFORMANCE,
        locks=locks,
    )
    authorization = StageAuthorization(
        authorization_id=StageAuthorizationId.new(),
        stage_id=entry.stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=entry.protocol_id,
        entry_bundle_sha256=entry.digest,
        envelope_sha256=entry.envelope_sha256,
        authorizer_id="operator-1",
        authorizer_role="operator",
        valid_from=NOW - timedelta(minutes=1),
        valid_until=NOW + timedelta(minutes=1),
        paid_execution=True,
    )
    return (
        seal_integration_plan(
            entry_bundle=entry,
            conformance_exit=conformance_exit,
            authorization=authorization,
            scenario_ids=_scenarios(),
            limits=limits,
            now=NOW,
        ),
        entry,
        limits,
    )


def test_integration_plan_seals_exact_32_cell_balance_and_opaque_schedule() -> None:
    plan, entry, limits = _plan()

    assert len(plan.runs) == INTEGRATION_RUN_COUNT
    assert len({run.scenario_id for run in plan.runs}) == INTEGRATION_SCENARIO_COUNT
    assert {run.model_role for run in plan.runs} == {"R-COP-M0"}
    assert {run.concurrency_lane for run in plan.runs} == {0, 1}
    assert all(
        {plan.runs[index].condition_slot, plan.runs[index + 1].condition_slot} == {0, 1}
        for index in range(0, INTEGRATION_RUN_COUNT, 2)
    )
    assert load_integration_plan(plan.bytes()) == plan
    authorize_integration_plan(entry, plan)
    assert limits.task_agent_token_cap == 3_200_000
    assert limits.framework_input_token_cap == 600_000
    assert limits.framework_output_token_cap == 200_000
    assert limits.per_run_active_seconds == 900
    assert limits.elapsed_seconds_cap == 28_800


@pytest.mark.parametrize(
    "runs",
    (lambda plan: plan.runs[:-1], lambda plan: (*plan.runs, plan.runs[0])),
)
def test_integration_plan_rejects_missing_or_duplicate_scenario_cells(runs) -> None:
    plan, _entry, _limits = _plan()

    with pytest.raises(StageControlError) as failure:
        replace(plan, runs=runs(plan))
    assert failure.value.code == "integration_enrollment_envelope_invalid"


def test_integration_limits_project_to_shared_breaker_envelopes() -> None:
    plan, _entry, limits = _plan()

    assert plan.runs
    assert limits.stage_envelope(HASH).scope == "stage"
    assert limits.run_envelope(HASH).scope == "run"
    with pytest.raises(StageControlError, match="integration budget envelope invalid"):
        IntegrationStageLimits(
            ai_credit_cap=100.0,
            usd_cap=10.0,
            per_run_tool_call_cap=60,
            concurrency_cap=3,
        )
