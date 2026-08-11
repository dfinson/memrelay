from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageAuthorizationId, StageId
from memrelay_eval.domain.policies import STAGE_ENTRY_LOCK_FIELDS
from memrelay_eval.domain.states import StageKind, StageState
from memrelay_eval.orchestration.limits import (
    PrimaryModelStageLimits,
    SecondaryModelStageLimits,
)
from memrelay_eval.orchestration.stages import (
    StageAuthorization,
    StageEntryBundle,
    StageExitBundle,
    conclude_primary_stage,
    resume_unstarted_model_units,
    seal_primary_stage_plan,
    seal_secondary_stage_plan,
)

_HASH = "a" * 64
_NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def _locks(preceding: str, limits_sha256: str = _HASH) -> dict[str, str]:
    result = dict.fromkeys(STAGE_ENTRY_LOCK_FIELDS, _HASH)
    result["preceding_exit_sha256"] = preceding
    result["limits_sha256"] = limits_sha256
    return result


def _exit(kind: StageKind, preceding: str = "b" * 64) -> StageExitBundle:
    return StageExitBundle(
        stage_id=StageId.new(),
        stage_kind=kind,
        protocol_id=ProtocolId.new(),
        entry_bundle_sha256="c" * 64,
        preceding_exit_sha256=preceding,
        status=StageState.ACCEPTED,
        reconciliation_sha256="d" * 64,
        inclusion_decision_sha256="e" * 64,
        authorization_id=StageAuthorizationId.new(),
    )


def _authorization(entry: StageEntryBundle) -> StageAuthorization:
    return StageAuthorization(
        authorization_id=StageAuthorizationId.new(),
        stage_id=entry.stage_id,
        stage_kind=entry.stage_kind,
        protocol_id=entry.protocol_id,
        entry_bundle_sha256=entry.digest,
        envelope_sha256=entry.envelope_sha256,
        authorizer_id="operator-1",
        authorizer_role="operator",
        valid_from=_NOW - timedelta(minutes=1),
        valid_until=_NOW + timedelta(minutes=1),
        paid_execution=True,
    )


def _primary_plan():
    pilot_exit = _exit(StageKind.PILOT)
    limits = _primary_limits()
    entry = StageEntryBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.PRIMARY,
        protocol_id=ProtocolId.new(),
        predecessor_stage_kind=StageKind.PILOT,
        locks=_locks(pilot_exit.digest, limits.digest),
    )
    families = {
        f"F{family}": tuple(f"task-{family}-{number}" for number in range(4))
        for family in range(1, 9)
    }
    return (
        seal_primary_stage_plan(
            entry_bundle=entry,
            pilot_exit=pilot_exit,
            authorization=_authorization(entry),
            now=_NOW,
            task_families=families,
            limits=limits,
        ),
        pilot_exit,
        entry,
    )


def _conclusion(plan):
    return conclude_primary_stage(
        plan=plan,
        terminal_unit_ids=tuple(unit.unit_id for unit in plan.units),
        reconciliation_sha256="f" * 64,
        exit_evidence_sha256={
            "analysis": "1" * 64,
            "harm_tails": "2" * 64,
            "panel": "3" * 64,
            "pareto_surface": "4" * 64,
            "safety": "5" * 64,
            "simultaneous_intervals": "6" * 64,
        },
        claim_decisions=(
            SimpleNamespace(decision_sha256="7" * 64, status="blocked"),
            SimpleNamespace(decision_sha256="8" * 64, status="estimation-only"),
        ),
    )


def _secondary_inputs(primary_exit: StageExitBundle):
    limits = _secondary_limits()
    entries = {
        role: StageEntryBundle(
            stage_id=StageId.new(),
            stage_kind=StageKind.SECONDARY,
            protocol_id=ProtocolId.new(),
            predecessor_stage_kind=StageKind.PRIMARY,
            locks=_locks(primary_exit.digest, limits.digest),
        )
        for role in ("M1", "M2")
    }
    return entries, {role: _authorization(entry) for role, entry in entries.items()}, limits


def test_primary_plan_is_exact_itt_and_terminal_exit_is_complete() -> None:
    plan, _pilot_exit, _entry = _primary_plan()

    assert len(plan.task_ids) == 32
    assert len(plan.units) == 512
    assert {unit.model_role for unit in plan.units} == {"M0"}
    assert {unit.concurrency_lane for unit in plan.units} == {0, 1, 2, 3}
    assert _primary_limits().task_agent_token_cap == 128_000_000
    assert _primary_limits().concurrency_cap == 4
    conclusion = _conclusion(plan)
    assert conclusion.claim_statuses == ("blocked", "estimation-only")

    with pytest.raises(StageControlError, match="primary itt incomplete"):
        conclude_primary_stage(
            plan=plan,
            terminal_unit_ids=tuple(unit.unit_id for unit in plan.units[:-1]),
            reconciliation_sha256="f" * 64,
            exit_evidence_sha256=_exit_evidence(),
            claim_decisions=(SimpleNamespace(decision_sha256="7" * 64, status="pass"),),
        )
    with pytest.raises(StageControlError, match="primary itt incomplete"):
        conclude_primary_stage(
            plan=plan,
            terminal_unit_ids=tuple(unit.unit_id for unit in plan.units) + ("unknown",),
            reconciliation_sha256="f" * 64,
            exit_evidence_sha256=_exit_evidence(),
            claim_decisions=(SimpleNamespace(decision_sha256="7" * 64, status="pass"),),
        )


def test_secondary_is_role_stratified_and_records_unavailable_role() -> None:
    plan, pilot_exit, primary_entry = _primary_plan()
    conclusion = _conclusion(plan)
    primary_exit = StageExitBundle(
        stage_id=primary_entry.stage_id,
        stage_kind=StageKind.PRIMARY,
        protocol_id=primary_entry.protocol_id,
        entry_bundle_sha256=primary_entry.digest,
        preceding_exit_sha256=pilot_exit.digest,
        status=StageState.ACCEPTED,
        reconciliation_sha256=conclusion.reconciliation_sha256,
        inclusion_decision_sha256="9" * 64,
        authorization_id=StageAuthorizationId.new(),
    )
    entries, authorizations, limits = _secondary_inputs(primary_exit)
    subset = {family: task_ids[:2] for family, task_ids in plan.task_families.items()}

    secondary = seal_secondary_stage_plan(
        primary_plan=plan,
        primary_exit=primary_exit,
        primary_conclusion=conclusion,
        role_availability={"M1": "qualified", "M2": "drifted"},
        role_entry_bundles=entries,
        role_authorizations=authorizations,
        now=_NOW,
        task_families=subset,
        limits=limits,
    )

    assert secondary.total_units == 96
    assert [role.model_role for role in secondary.role_plans] == ["M1"]
    assert secondary.unavailable_roles == {"M2": "drifted"}
    assert secondary.role_plans[0].stratum_id == "secondary-M1"


def test_secondary_records_no_qualified_role_without_substitution() -> None:
    plan, pilot_exit, primary_entry = _primary_plan()
    conclusion = _conclusion(plan)
    primary_exit = StageExitBundle(
        stage_id=primary_entry.stage_id,
        stage_kind=StageKind.PRIMARY,
        protocol_id=primary_entry.protocol_id,
        entry_bundle_sha256=primary_entry.digest,
        preceding_exit_sha256=pilot_exit.digest,
        status=StageState.ACCEPTED,
        reconciliation_sha256=conclusion.reconciliation_sha256,
        inclusion_decision_sha256="9" * 64,
        authorization_id=StageAuthorizationId.new(),
    )
    subset = {family: task_ids[:2] for family, task_ids in plan.task_families.items()}

    secondary = seal_secondary_stage_plan(
        primary_plan=plan,
        primary_exit=primary_exit,
        primary_conclusion=conclusion,
        role_availability={"M1": "unavailable", "M2": "drifted"},
        role_entry_bundles={},
        role_authorizations={},
        now=_NOW,
        task_families=subset,
        limits=_secondary_limits(),
    )

    assert secondary.total_units == 0
    assert secondary.unavailable_roles == {"M1": "unavailable", "M2": "drifted"}


def test_secondary_caps_two_strata_and_resume_never_restarts_started_units() -> None:
    plan, pilot_exit, primary_entry = _primary_plan()
    conclusion = _conclusion(plan)
    primary_exit = StageExitBundle(
        stage_id=primary_entry.stage_id,
        stage_kind=StageKind.PRIMARY,
        protocol_id=primary_entry.protocol_id,
        entry_bundle_sha256=primary_entry.digest,
        preceding_exit_sha256=pilot_exit.digest,
        status=StageState.ACCEPTED,
        reconciliation_sha256=conclusion.reconciliation_sha256,
        inclusion_decision_sha256="9" * 64,
        authorization_id=StageAuthorizationId.new(),
    )
    entries, authorizations, limits = _secondary_inputs(primary_exit)
    subset = {family: task_ids[:2] for family, task_ids in plan.task_families.items()}
    secondary = seal_secondary_stage_plan(
        primary_plan=plan,
        primary_exit=primary_exit,
        primary_conclusion=conclusion,
        role_availability={"M1": "qualified", "M2": "qualified"},
        role_entry_bundles=entries,
        role_authorizations=authorizations,
        now=_NOW,
        task_families=subset,
        limits=limits,
    )

    assert secondary.total_units == 192
    assert _secondary_limits().for_role("M1") == {
        "task_agent_token_cap": 24_000_000,
        "framework_input_token_cap": 4_500_000,
        "framework_output_token_cap": 1_500_000,
    }
    started = tuple(unit.unit_id for unit in secondary.role_plans[0].units[:3])
    resumed = resume_unstarted_model_units(
        secondary.role_plans[0].units,
        started_unit_ids=started,
        locks_verified=True,
        receipts_consistent=True,
        model_healthy=True,
    )
    assert len(resumed) == 93
    assert not set(resumed) & set(started)
    with pytest.raises(StageControlError, match="model unavailable pause"):
        resume_unstarted_model_units(
            secondary.role_plans[0].units,
            started_unit_ids=(),
            locks_verified=True,
            receipts_consistent=True,
            model_healthy=False,
        )


def _exit_evidence() -> dict[str, str]:
    return {
        "analysis": "1" * 64,
        "harm_tails": "2" * 64,
        "panel": "3" * 64,
        "pareto_surface": "4" * 64,
        "safety": "5" * 64,
        "simultaneous_intervals": "6" * 64,
    }


def _primary_limits() -> PrimaryModelStageLimits:
    return PrimaryModelStageLimits(
        ai_credit_cap=10.0,
        usd_cap=20.0,
        task_class_active_seconds={"default": 60.0},
    )


def _secondary_limits() -> SecondaryModelStageLimits:
    return SecondaryModelStageLimits(
        ai_credit_cap=10.0,
        usd_cap=20.0,
        task_class_active_seconds={"default": 60.0},
    )
