from __future__ import annotations

from datetime import UTC, datetime

import pytest
from memrelay_eval.domain.ids import (
    AuthorizationId,
    AuthorizationVersionId,
    PolicyVersionId,
    PrincipalId,
    PurposeId,
    PurposeVersionId,
    RepositoryId,
)
from memrelay_eval.evidence.governance import (
    REQUIRED_DGR_CONTROLS,
    DgrBundle,
    DgrControl,
    DgrProof,
    DgrProofStatus,
    load_dgr_bundle,
)

_NOW = datetime(2026, 8, 11, tzinfo=UTC)
_HASH = "a" * 64

TEA_IDS = {
    DgrControl.FIELD_REGISTRY: "GOV-FIELD-REGISTRY",
    DgrControl.CROSS_REPOSITORY_RECORD: "GOV-XREPO-RECORD",
    DgrControl.CALLER_AUTHENTICATION: "GOV-CALLER-AUTH",
    DgrControl.PRINCIPAL_BINDING: "GOV-PRINCIPAL-BIND",
    DgrControl.CONFUSED_DEPUTY: "GOV-CONFUSED-DEPUTY",
    DgrControl.AUTHORIZATION_CACHE: "GOV-AUTH-CACHE",
    DgrControl.POLICY_TOCTOU: "GOV-POLICY-TOCTOU",
    DgrControl.WITHDRAWAL: "GOV-WITHDRAW",
    DgrControl.REVOCATION: "GOV-REVOKE",
    DgrControl.BACKUP_RESTORE_REVOKED: "GOV-BACKUP-RESTORE-REVOKED",
    DgrControl.MIGRATION: "GOV-MIGRATE",
    DgrControl.DELETE_GRAPH: "GOV-DELETE-GRAPH",
    DgrControl.DELETE_DERIVED: "GOV-DELETE-DERIVED",
    DgrControl.BACKUP_EXPIRY: "GOV-BACKUP-EXPIRY",
    DgrControl.KEY_DESTRUCTION: "GOV-KEY-DESTRUCTION",
    DgrControl.VIEWER_PURGE: "GOV-VIEWER-PURGE",
    DgrControl.DOWNSTREAM_RECEIPT: "GOV-DOWNSTREAM-RECEIPT",
    DgrControl.EXISTING_GRAPH: "GOV-EXISTING-GRAPH",
    DgrControl.REPOSITORY_PROVENANCE: "GOV-REPOSITORY-PROVENANCE",
    DgrControl.DATA_CLASSIFICATION: "GOV-DATA-CLASSIFICATION",
    DgrControl.AUDIT: "GOV-AUDIT",
}
FROZEN_TEA_IDS = frozenset(
    {
        *TEA_IDS.values(),
        "SCOPE-R-AUTH",
        "OPS-STAGE-ENVELOPE",
        "BLOCK-GOVERNANCE",
    }
)


def make_bundle(*, status: DgrProofStatus = DgrProofStatus.PASSED) -> DgrBundle:
    repositories = tuple(
        RepositoryId.from_digest(f"{number:02x}" + "0" * 62) for number in range(24)
    )
    policy = PolicyVersionId.new()
    proofs = tuple(
        DgrProof(
            control=control,
            repository_id=repository,
            issuer_id="synthetic-issuer",
            authority_id="synthetic-governance-authority",
            policy_version=str(policy),
            schema_version="1.0.0",
            proof_version="1.0.0",
            policy_sha256=_HASH,
            schema_sha256=_HASH,
            evidence_sha256=_HASH,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_until=datetime(2027, 1, 1, tzinfo=UTC),
            revocation_generation=7,
            status=status,
        )
        for repository in repositories
        for control in REQUIRED_DGR_CONTROLS
    )
    return DgrBundle(
        principal_id=str(PrincipalId.new()),
        authorization_id=str(AuthorizationId.new()),
        authorization_version=str(AuthorizationVersionId.new()),
        purpose_id=str(PurposeId.new()),
        purpose_version=str(PurposeVersionId.new()),
        policy_version=str(policy),
        revocation_generation=7,
        proofs=proofs,
    )


@pytest.mark.parametrize("control,tea_id", tuple(TEA_IDS.items()))
def test_each_frozen_governance_control_has_a_typed_repository_scoped_proof(
    control: DgrControl, tea_id: str
) -> None:
    bundle = make_bundle()

    matching = [proof for proof in bundle.proofs if proof.control is control]

    assert tea_id.startswith("GOV-")
    assert len(matching) == 24
    assert all(proof.is_current(_NOW, revocation_generation=7) for proof in matching)


def test_dgr_bundle_is_canonical_complete_and_current_for_every_cluster_scope() -> None:
    bundle = make_bundle()

    loaded = load_dgr_bundle(bundle.bytes())

    assert loaded.digest == bundle.digest
    assert len(loaded.repository_ids) == 24
    assert all(
        loaded.is_current(_NOW, repository_id=repository) for repository in loaded.repository_ids
    )


def test_failed_or_revoked_control_cannot_be_current() -> None:
    bundle = make_bundle(status=DgrProofStatus.REVOKED)

    assert bundle.is_current(_NOW) is False


def test_frozen_tea_registry_keeps_scope_envelope_and_blocker_objectives() -> None:
    assert {"SCOPE-R-AUTH", "OPS-STAGE-ENVELOPE", "BLOCK-GOVERNANCE"} <= FROZEN_TEA_IDS
