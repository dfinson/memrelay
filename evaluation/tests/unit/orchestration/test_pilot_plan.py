from __future__ import annotations

import pytest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageId
from memrelay_eval.domain.states import StageKind
from memrelay_eval.orchestration.pilot import (
    PILOT_EVIDENCE_CLASSIFICATION,
    FrozenPilotPlan,
    PilotBudget,
    PilotContracts,
    PilotTask,
    authorize_pilot_plan,
    load_pilot_plan,
)
from memrelay_eval.orchestration.stages import StageEntryBundle

HASH = "a" * 64


def _plan() -> FrozenPilotPlan:
    tasks = tuple(
        PilotTask(
            task_id=f"task-{task:02}",
            assignment_unit_ids=tuple(f"unit-{task:02}-{unit}" for unit in range(8)),
            session_ids_by_unit={
                f"unit-{task:02}-{unit}": (
                    f"session-{task:02}-{unit}-a",
                    f"session-{task:02}-{unit}-b",
                )
                for unit in range(8)
            },
        )
        for task in range(16)
    )
    return FrozenPilotPlan(
        stage_id=StageId.new(),
        protocol_id=ProtocolId.new(),
        tasks=tasks,
        contracts=PilotContracts(**dict.fromkeys(PilotContracts.__dataclass_fields__, HASH)),
        budget=PilotBudget(1.0, 1.0, {"standard": 1}),
    )


def test_sealed_plan_has_exactly_128_assignment_units_and_is_non_confirmatory() -> None:
    plan = _plan()

    assert sum(len(task.assignment_unit_ids) for task in plan.tasks) == 128
    assert plan.evidence_classification == PILOT_EVIDENCE_CLASSIFICATION
    assert load_pilot_plan(plan.bytes()) == plan


def test_plan_rejects_any_non_eight_unit_task() -> None:
    with pytest.raises(StageControlError) as failure:
        PilotTask(
            task_id="replacement",
            assignment_unit_ids=tuple(f"replacement-{index}" for index in range(7)),
            session_ids_by_unit={
                f"replacement-{index}": (f"a-{index}", f"b-{index}") for index in range(7)
            },
        )
    assert failure.value.code == "pilot_task_assignment_shape_invalid"


def test_plan_binds_the_stage_locks_and_refuses_drift() -> None:
    plan = _plan()
    locks = dict.fromkeys(
        (
            "catalog_sha256",
            "protocol_sha256",
            "sdk_sha256",
            "runtime_lock_sha256",
            "model_lock_sha256",
            "environment_sha256",
            "grader_sha256",
            "judge_sha256",
            "telemetry_sha256",
            "price_table_sha256",
            "limits_sha256",
            "preceding_exit_sha256",
        ),
        HASH,
    )
    locks.update(
        {
            "catalog_sha256": plan.contracts.catalog_sha256,
            "environment_sha256": plan.contracts.configuration_sha256,
            "model_lock_sha256": plan.contracts.model_lock_sha256,
            "price_table_sha256": plan.contracts.price_table_sha256,
            "limits_sha256": plan.contracts.limits_sha256,
        }
    )
    entry = StageEntryBundle(
        stage_id=plan.stage_id,
        stage_kind=StageKind.PILOT,
        protocol_id=plan.protocol_id,
        predecessor_stage_kind=StageKind.INTEGRATION,
        locks=locks,
    )

    authorize_pilot_plan(entry, plan)
    locks["limits_sha256"] = "b" * 64
    drifted = StageEntryBundle(
        stage_id=plan.stage_id,
        stage_kind=StageKind.PILOT,
        protocol_id=plan.protocol_id,
        predecessor_stage_kind=StageKind.INTEGRATION,
        locks=locks,
    )
    with pytest.raises(StageControlError) as failure:
        authorize_pilot_plan(drifted, plan)
    assert failure.value.code == "pilot_plan_lock_mismatch"
