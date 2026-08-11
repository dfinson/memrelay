"""Fault coverage for frozen model-stage allocation and model-role admission."""

from __future__ import annotations

import pytest
from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import ConformancePauseError, StageControlError
from memrelay_eval.orchestration.control import verified_secondary_role_qualifications
from memrelay_eval.orchestration.stages import resume_unstarted_model_units
from tests.unit.orchestration.test_primary_secondary_plans import _model_lock, _primary_plan


def test_secondary_model_lock_rejects_alias_and_integrity_drift() -> None:
    alias = _model_lock(("M0", "M1"), {"M2": "unavailable"})
    selected = alias["selected_models"]
    assert isinstance(selected, list)
    selected[1]["native_id"] = selected[0]["native_id"]
    with pytest.raises(ConformancePauseError, match="digest"):
        verified_secondary_role_qualifications(alias)

    valid_alias = _model_lock(("M0", "M1"), {"M2": "unavailable"})
    selected = valid_alias["selected_models"]
    assert isinstance(selected, list)
    selected[1]["native_id"] = selected[0]["native_id"]
    valid_alias["lock_sha256"] = canonical_digest(
        {key: value for key, value in valid_alias.items() if key != "lock_sha256"}
    )
    with pytest.raises(ConformancePauseError, match="reused"):
        verified_secondary_role_qualifications(valid_alias)


@pytest.mark.parametrize(
    ("locks_verified", "receipts_consistent", "model_healthy", "code"),
    (
        (False, True, True, "resume lock drift"),
        (True, False, True, "resume receipt conflict"),
        (True, True, False, "model unavailable pause"),
    ),
)
def test_resume_never_restarts_terminal_or_started_units(
    locks_verified: bool, receipts_consistent: bool, model_healthy: bool, code: str
) -> None:
    plan, _pilot_exit, _entry = _primary_plan()
    started = tuple(unit.unit_id for unit in plan.units[:2])
    with pytest.raises(StageControlError, match=code):
        resume_unstarted_model_units(
            plan.units,
            started_unit_ids=started,
            locks_verified=locks_verified,
            receipts_consistent=receipts_consistent,
            model_healthy=model_healthy,
        )
