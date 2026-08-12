from __future__ import annotations

import pytest
from memrelay_eval.domain.errors import AnalysisError
from memrelay_eval.domain.repositories import (
    CROSS_REPOSITORY_CLUSTER_UNIT,
    require_cross_repository_cluster_itt,
)
from tests.contract.test_cross_repository_stage_gate import _plan


def _units() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "cluster_id": cluster.cluster_id,
            "assignment_unit": CROSS_REPOSITORY_CLUSTER_UNIT,
            "experimental_unit": CROSS_REPOSITORY_CLUSTER_UNIT,
            "resampling_unit": CROSS_REPOSITORY_CLUSTER_UNIT,
            "analysis_unit": CROSS_REPOSITORY_CLUSTER_UNIT,
            "itt_retained": True,
            "attrition_status": "retained",
            "authorization_status": "revoked" if index == 0 else "authorized",
            "revocation_status": "revoked" if index == 0 else "active",
        }
        for index, cluster in enumerate(_plan().clusters)
    )


def test_cluster_itt_retains_revocation_and_attrition_at_repository_cluster_level() -> None:
    plan = _plan()

    require_cross_repository_cluster_itt(_units(), plan=plan)


def test_cluster_itt_rejects_session_pseudo_replication_and_partial_release() -> None:
    plan = _plan()
    units = list(_units())
    units[0]["analysis_unit"] = "session"

    with pytest.raises(AnalysisError, match="cluster itt invalid"):
        require_cross_repository_cluster_itt(units, plan=plan)

    with pytest.raises(AnalysisError, match="cluster count invalid"):
        require_cross_repository_cluster_itt(_units()[:-1], plan=plan)
