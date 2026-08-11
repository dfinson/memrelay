"""Frozen repository-relation cluster contracts for the DG-R stage."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from memrelay_eval.canonical import attach_digest, canonical_bytes, canonical_digest, verify_digest

from .errors import AnalysisError, StageControlError
from .ids import RepositoryId

CROSS_REPOSITORY_CLUSTER_COUNT = 24
CROSS_REPOSITORY_CLUSTER_UNIT = "repository_relation_cluster"


@dataclass(frozen=True, slots=True)
class RepositoryRelationCluster:
    """One opaque relation is the sole assignment and analysis unit."""

    cluster_id: str
    repository_id: RepositoryId
    relation_sha256: str
    arm: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.cluster_id, str)
            or not self.cluster_id
            or not isinstance(self.repository_id, RepositoryId)
            or not isinstance(self.arm, str)
            or not self.arm
            or len(self.relation_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.relation_sha256)
        ):
            raise StageControlError("cross_repository_cluster_invalid")

    def to_document(self) -> dict[str, str]:
        return {
            "cluster_id": self.cluster_id,
            "repository_id": str(self.repository_id),
            "relation_sha256": self.relation_sha256,
            "arm": self.arm,
        }


@dataclass(frozen=True, slots=True)
class CrossRepositoryStagePlan:
    """A pre-assignment, exact-N plan with no substitution path."""

    dgr_bundle_sha256: str
    primary_conclusion_sha256: str
    clusters: tuple[RepositoryRelationCluster, ...]
    protocol_sha256: str
    catalog_sha256: str
    model_sha256: str
    environment_sha256: str
    limits_sha256: str
    estimator_sha256: str
    missingness_policy_sha256: str

    def __post_init__(self) -> None:
        values = (
            self.dgr_bundle_sha256,
            self.primary_conclusion_sha256,
            self.protocol_sha256,
            self.catalog_sha256,
            self.model_sha256,
            self.environment_sha256,
            self.limits_sha256,
            self.estimator_sha256,
            self.missingness_policy_sha256,
        )
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in values
        ):
            raise StageControlError("cross_repository_plan_hash_invalid")
        clusters = tuple(sorted(self.clusters, key=lambda item: item.cluster_id))
        if len(clusters) != CROSS_REPOSITORY_CLUSTER_COUNT:
            raise StageControlError("cross_repository_cluster_count_invalid")
        if (
            len({cluster.cluster_id for cluster in clusters}) != CROSS_REPOSITORY_CLUSTER_COUNT
            or len({cluster.repository_id for cluster in clusters})
            != CROSS_REPOSITORY_CLUSTER_COUNT
            or len({cluster.relation_sha256 for cluster in clusters})
            != CROSS_REPOSITORY_CLUSTER_COUNT
            or {cluster.arm for cluster in clusters} != {"control", "memory"}
            or sum(cluster.arm == "control" for cluster in clusters)
            != CROSS_REPOSITORY_CLUSTER_COUNT // 2
        ):
            raise StageControlError("cross_repository_cluster_substitution_forbidden")
        object.__setattr__(self, "clusters", clusters)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "artifact_type": "cross_repository_stage_plan",
            "cluster_count": CROSS_REPOSITORY_CLUSTER_COUNT,
            "cluster_unit": CROSS_REPOSITORY_CLUSTER_UNIT,
            "dgr_bundle_sha256": self.dgr_bundle_sha256,
            "primary_conclusion_sha256": self.primary_conclusion_sha256,
            "protocol_sha256": self.protocol_sha256,
            "catalog_sha256": self.catalog_sha256,
            "model_sha256": self.model_sha256,
            "environment_sha256": self.environment_sha256,
            "limits_sha256": self.limits_sha256,
            "estimator_sha256": self.estimator_sha256,
            "missingness_policy_sha256": self.missingness_policy_sha256,
            "clusters": [cluster.to_document() for cluster in self.clusters],
        }

    def bytes(self) -> bytes:
        return canonical_bytes(attach_digest(self.to_document()))


def load_cross_repository_stage_plan(data: bytes) -> CrossRepositoryStagePlan:
    """Load only a canonical, digest-bound exact-24 cluster plan."""

    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StageControlError("cross_repository_plan_corrupt") from error
    if (
        not isinstance(document, dict)
        or document.get("artifact_type") != "cross_repository_stage_plan"
        or not verify_digest(document)
        or canonical_bytes(document) != data
    ):
        raise StageControlError("cross_repository_plan_corrupt")
    try:
        plan = CrossRepositoryStagePlan(
            dgr_bundle_sha256=document["dgr_bundle_sha256"],
            primary_conclusion_sha256=document["primary_conclusion_sha256"],
            clusters=tuple(
                RepositoryRelationCluster(
                    cluster_id=item["cluster_id"],
                    repository_id=RepositoryId(item["repository_id"]),
                    relation_sha256=item["relation_sha256"],
                    arm=item["arm"],
                )
                for item in document["clusters"]
            ),
            protocol_sha256=document["protocol_sha256"],
            catalog_sha256=document["catalog_sha256"],
            model_sha256=document["model_sha256"],
            environment_sha256=document["environment_sha256"],
            limits_sha256=document["limits_sha256"],
            estimator_sha256=document["estimator_sha256"],
            missingness_policy_sha256=document["missingness_policy_sha256"],
        )
    except (KeyError, TypeError, ValueError, StageControlError) as error:
        raise StageControlError("cross_repository_plan_corrupt") from error
    if plan.bytes() != data:
        raise StageControlError("cross_repository_plan_corrupt")
    return plan


def require_cross_repository_cluster_itt(
    assigned_units: Iterable[dict[str, object]], *, plan: CrossRepositoryStagePlan
) -> None:
    """Reject pseudo-replication and retain every assigned cluster in ITT."""

    units = tuple(assigned_units)
    if len(units) != CROSS_REPOSITORY_CLUSTER_COUNT:
        raise AnalysisError("cross_repository_cluster_count_invalid")
    expected = {cluster.cluster_id for cluster in plan.clusters}
    observed: set[str] = set()
    for unit in units:
        cluster_id = unit.get("cluster_id")
        if (
            not isinstance(cluster_id, str)
            or cluster_id in observed
            or cluster_id not in expected
            or any(
                unit.get(key) != CROSS_REPOSITORY_CLUSTER_UNIT
                for key in (
                    "assignment_unit",
                    "experimental_unit",
                    "resampling_unit",
                    "analysis_unit",
                )
            )
            or unit.get("itt_retained") is not True
            or "attrition_status" not in unit
            or "authorization_status" not in unit
            or "revocation_status" not in unit
        ):
            raise AnalysisError("cross_repository_cluster_itt_invalid")
        observed.add(cluster_id)
    if observed != expected:
        raise AnalysisError("cross_repository_cluster_substitution_forbidden")
