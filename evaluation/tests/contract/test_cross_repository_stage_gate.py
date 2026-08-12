from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from memrelay_eval.cli.main import main
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageAuthorizationId, StageId
from memrelay_eval.domain.policies import STAGE_ENTRY_LOCK_FIELDS
from memrelay_eval.domain.repositories import (
    CROSS_REPOSITORY_CLUSTER_COUNT,
    CROSS_REPOSITORY_CLUSTER_UNIT,
    CrossRepositoryStagePlan,
    RepositoryRelationCluster,
)
from memrelay_eval.domain.states import StageKind, StageState
from memrelay_eval.orchestration.stages import (
    PrimaryStageConclusion,
    StageAuthorization,
    StageEntryBundle,
    StageExitBundle,
    authorize_stage_entry,
)

from tests.contract.test_dgr_qualification import _HASH, _NOW, make_bundle

_PRIMARY_EVIDENCE = (
    "simultaneous_intervals",
    "harm_tails",
    "pareto_surface",
    "panel",
    "safety",
    "analysis",
)
_CI_ENV_MARKERS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "JENKINS_URL",
    "TF_BUILD",
    "TEAMCITY_VERSION",
    "CIRCLECI",
)


def _plan(bundle=None) -> CrossRepositoryStagePlan:
    bundle = bundle or make_bundle()
    return CrossRepositoryStagePlan(
        dgr_bundle_sha256=bundle.digest,
        primary_conclusion_sha256=_HASH,
        protocol_sha256=_HASH,
        catalog_sha256=_HASH,
        model_sha256=_HASH,
        environment_sha256=_HASH,
        limits_sha256=_HASH,
        estimator_sha256=_HASH,
        missingness_policy_sha256=_HASH,
        clusters=tuple(
            RepositoryRelationCluster(
                cluster_id=f"cluster-{number:02}",
                repository_id=repository,
                relation_sha256=f"{number + 100:064x}",
                arm="memory" if number % 2 else "control",
            )
            for number, repository in enumerate(bundle.repository_ids)
        ),
    )


def _entry(plan: CrossRepositoryStagePlan, predecessor: StageExitBundle) -> StageEntryBundle:
    locks = dict.fromkeys(STAGE_ENTRY_LOCK_FIELDS, _HASH)
    locks["preceding_exit_sha256"] = predecessor.digest
    return StageEntryBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.CROSS_REPOSITORY,
        protocol_id=ProtocolId.new(),
        predecessor_stage_kind=StageKind.PRIMARY,
        locks=locks,
        dgr_bundle_sha256=plan.dgr_bundle_sha256,
        cross_repository_plan_sha256=plan.digest,
    )


def test_cross_repository_gate_requires_exact_frozen_24_cluster_plan() -> None:
    plan = _plan()

    assert len(plan.clusters) == CROSS_REPOSITORY_CLUSTER_COUNT
    assert {CROSS_REPOSITORY_CLUSTER_UNIT} == {CROSS_REPOSITORY_CLUSTER_UNIT}
    assert len({cluster.repository_id for cluster in plan.clusters}) == 24


@pytest.mark.parametrize("count", (23, 25))
def test_cross_repository_gate_rejects_cluster_count_drift(count: int) -> None:
    plan = _plan()

    clusters = (
        plan.clusters[:count]
        if count == 23
        else plan.clusters
        + (
            RepositoryRelationCluster(
                "extra",
                plan.clusters[0].repository_id,
                "f" * 64,
                "memory",
            ),
        )
    )
    with pytest.raises(StageControlError, match="cluster"):
        replace(plan, clusters=clusters)


def test_cross_repository_gate_rejects_unbalanced_assignment() -> None:
    plan = _plan()

    with pytest.raises(StageControlError, match="substitution"):
        replace(
            plan,
            clusters=tuple(replace(cluster, arm="memory") for cluster in plan.clusters),
        )


def test_cross_repository_stage_entry_binds_dgr_and_operator_authorization() -> None:
    bundle = make_bundle()
    plan = _plan(bundle)
    predecessor = StageExitBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.PRIMARY,
        protocol_id=ProtocolId.new(),
        entry_bundle_sha256=_HASH,
        preceding_exit_sha256=_HASH,
        status=StageState.ACCEPTED,
        reconciliation_sha256=_HASH,
        inclusion_decision_sha256=_HASH,
        authorization_id=StageAuthorizationId.new(),
    )
    entry = _entry(plan, predecessor)
    authorization = StageAuthorization(
        authorization_id=StageAuthorizationId.new(),
        stage_id=entry.stage_id,
        stage_kind=StageKind.CROSS_REPOSITORY,
        protocol_id=entry.protocol_id,
        entry_bundle_sha256=entry.digest,
        envelope_sha256=entry.envelope_sha256,
        authorizer_id="separate-operator",
        authorizer_role="operator",
        valid_from=_NOW - timedelta(minutes=1),
        valid_until=_NOW + timedelta(minutes=1),
        paid_execution=True,
    )

    authorize_stage_entry(
        stage_kind=StageKind.CROSS_REPOSITORY,
        entry_bundle=entry,
        predecessor_exit=predecessor,
        authorization=authorization,
        now=_NOW,
        dgr_bundle=bundle,
        cross_repository_plan=plan,
    )


def test_cross_repository_cli_admits_only_the_complete_sealed_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for marker in _CI_ENV_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    bundle = make_bundle()
    predecessor = StageExitBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.PRIMARY,
        protocol_id=ProtocolId.new(),
        entry_bundle_sha256=_HASH,
        preceding_exit_sha256=_HASH,
        status=StageState.ACCEPTED,
        reconciliation_sha256=_HASH,
        inclusion_decision_sha256=_HASH,
        authorization_id=StageAuthorizationId.new(),
    )
    conclusion = PrimaryStageConclusion(
        primary_plan_sha256=_HASH,
        reconciliation_sha256=predecessor.reconciliation_sha256,
        exit_evidence_sha256=dict.fromkeys(_PRIMARY_EVIDENCE, _HASH),
        claim_decision_sha256=(_HASH,),
        claim_statuses=("pass",),
    )
    plan = replace(_plan(bundle), primary_conclusion_sha256=conclusion.digest)
    entry = _entry(plan, predecessor)
    authorization = StageAuthorization(
        authorization_id=StageAuthorizationId.new(),
        stage_id=entry.stage_id,
        stage_kind=StageKind.CROSS_REPOSITORY,
        protocol_id=entry.protocol_id,
        entry_bundle_sha256=entry.digest,
        envelope_sha256=entry.envelope_sha256,
        authorizer_id="separate-operator",
        authorizer_role="operator",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_until=datetime(2027, 1, 1, tzinfo=UTC),
        paid_execution=True,
    )
    paths = {
        "entry": tmp_path / "entry.json",
        "predecessor": tmp_path / "predecessor.json",
        "authorization": tmp_path / "authorization.json",
        "bundle": tmp_path / "bundle.json",
        "plan": tmp_path / "plan.json",
        "conclusion": tmp_path / "conclusion.json",
    }
    paths["entry"].write_bytes(entry.bytes())
    paths["predecessor"].write_bytes(predecessor.bytes())
    paths["authorization"].write_bytes(authorization.bytes())
    paths["bundle"].write_bytes(bundle.bytes())
    paths["plan"].write_bytes(plan.bytes())
    paths["conclusion"].write_bytes(conclusion.bytes())

    result = main(
        [
            "run",
            "--stage",
            "cross-repo",
            "--entry-bundle",
            str(paths["entry"]),
            "--predecessor-exit",
            str(paths["predecessor"]),
            "--authorization",
            str(paths["authorization"]),
            "--dgr-bundle",
            str(paths["bundle"]),
            "--cross-repository-plan",
            str(paths["plan"]),
            "--primary-conclusion",
            str(paths["conclusion"]),
            "--output-root",
            str(tmp_path / "artifacts"),
        ]
    )

    assert result == 0
