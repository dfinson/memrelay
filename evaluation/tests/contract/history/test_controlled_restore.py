"""Story 2.8: controlled immutable history restoration contract tests.

Covers AC1 (frozen bundle shape and no treatment-generated content), AC2 (byte-identical
restore across arms, parity hashes, and mismatch blocking), AC3 (probe-write policy and
controlled/dynamic non-pooling), and AC4 (fake ``ArtifactStorePort``/``LedgerPort`` unpaid
conformance only).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from memrelay_eval.adapters.fakes import (
    InMemoryArtifactStore,
    InMemoryLedger,
    InMemoryTreatmentPort,
)
from memrelay_eval.domain.entities import (
    GOLDEN_SETUP_PROVENANCE,
    ArtifactRef,
    ControlledHistoryBundle,
    ControlledHistoryItem,
)
from memrelay_eval.domain.errors import (
    ControlledEstimandPoolingError,
    ControlledHistoryMutationError,
    ControlledHistoryViolationError,
    ControlledRestoreMismatchError,
    InvalidArtifactManifestError,
)
from memrelay_eval.domain.ids import AttemptId, ExperimentId, HistoryId, ProtocolId, RunId
from memrelay_eval.domain.states import EvaluationStratum, HistoryMode, ProbeWriteDisposition
from memrelay_eval.orchestration.history import (
    ControlledHistoryBuilder,
    ControlledHistoryCoordinator,
    require_no_cross_regime_pooling,
    require_same_controlled_analysis_identity,
)
from memrelay_eval.orchestration.stages import enforce_controlled_effect_boundary

_VALID_FROM = datetime(2026, 1, 1, tzinfo=UTC)


def _item(
    position: int, content: bytes, *, provenance: str = GOLDEN_SETUP_PROVENANCE
) -> ControlledHistoryItem:
    return ControlledHistoryItem(
        position,
        ArtifactRef.from_bytes(content),
        f"actor_{position:032x}",
        "session",
        "rev-1",
        provenance,
        _VALID_FROM,
        None,
        "a" * 64,
    )


def _builder() -> tuple[ControlledHistoryBuilder, InMemoryArtifactStore]:
    store = InMemoryArtifactStore()
    return ControlledHistoryBuilder(store), store


def _bundle(
    builder: ControlledHistoryBuilder, *, history_id: HistoryId | None = None
) -> ControlledHistoryBundle:
    return builder.build_golden_checkpoint(
        history_id or HistoryId.new(),
        ProtocolId.new(),
        EvaluationStratum.PRODUCT,
        ExperimentId.new(),
        [_item(1, b"episode-one"), _item(2, b"episode-two")],
    )


def test_golden_bundle_preserves_ac1_fields_and_freezes_immutable_evidence() -> None:
    builder, store = _builder()
    history_id = HistoryId.new()
    protocol_id = ProtocolId.new()
    bundle = builder.build_golden_checkpoint(
        history_id,
        protocol_id,
        EvaluationStratum.DIRECT_ENGINE,
        ExperimentId.new(),
        [_item(1, b"episode-one"), _item(2, b"episode-two")],
    )

    assert bundle.history_id == history_id
    assert bundle.protocol_id == protocol_id
    assert bundle.schema_version == "1.0.0"
    assert [item.position for item in bundle.ordered_items] == [1, 2]
    for item in bundle.ordered_items:
        assert item.actor_id
        assert item.scope
        assert item.revision
        assert item.provenance == GOLDEN_SETUP_PROVENANCE
        assert item.expected_graph_input_sha256
    assert len(bundle.content_sha256) == 64

    # Freezing writes an immutable CAS artifact + ArtifactManifest of the bundle
    # itself (AC4 evidence): the exact canonical bytes of the bundle are retrievable
    # byte-for-byte from the same fake, unpaid-conformance ArtifactStorePort.
    from memrelay_eval.canonical import canonical_bytes

    expected_payload = canonical_bytes(bundle.to_record())
    stored_ref = ArtifactRef.from_bytes(expected_payload)
    assert store.open_verified(stored_ref) == expected_payload


def test_bundle_rejects_treatment_generated_provenance() -> None:
    with pytest.raises(InvalidArtifactManifestError):
        _item(1, b"treatment-authored", provenance="treatment_generated")


def test_golden_checkpoint_is_immutable_after_freeze() -> None:
    builder, _ = _builder()
    history_id = HistoryId.new()
    protocol_id = ProtocolId.new()
    experiment_id = ExperimentId.new()
    items = [_item(1, b"episode-one")]

    first = builder.build_golden_checkpoint(
        history_id, protocol_id, EvaluationStratum.PRODUCT, experiment_id, items
    )
    # Idempotent re-freeze with identical bytes returns the same bundle.
    second = builder.build_golden_checkpoint(
        history_id, protocol_id, EvaluationStratum.PRODUCT, experiment_id, items
    )
    assert first == second

    with pytest.raises(ControlledHistoryMutationError):
        builder.build_golden_checkpoint(
            history_id,
            protocol_id,
            EvaluationStratum.PRODUCT,
            experiment_id,
            [_item(1, b"mutated-episode")],
        )


def test_failed_freeze_evidence_write_never_commits_a_bundle_without_evidence() -> None:
    """A transient CAS failure while freezing must not silently mint an unbacked bundle."""

    class FlakyOnceArtifactStore(InMemoryArtifactStore):
        def __init__(self) -> None:
            super().__init__()
            self.write_manifest_calls = 0

        def write_manifest(self, manifest):  # noqa: ANN001 - test double
            self.write_manifest_calls += 1
            if self.write_manifest_calls == 1:
                raise RuntimeError("simulated transient CAS failure")
            return super().write_manifest(manifest)

    store = FlakyOnceArtifactStore()
    builder = ControlledHistoryBuilder(store)
    history_id = HistoryId.new()
    protocol_id = ProtocolId.new()
    experiment_id = ExperimentId.new()
    items = [_item(1, b"episode-one")]

    with pytest.raises(RuntimeError):
        builder.build_golden_checkpoint(
            history_id, protocol_id, EvaluationStratum.PRODUCT, experiment_id, items
        )

    # The failed freeze must not have left a "frozen" bundle with no CAS evidence.
    assert builder.frozen_bundle(history_id) is None

    # A retry after the transient failure clears succeeds and is now genuinely frozen.
    bundle = builder.build_golden_checkpoint(
        history_id, protocol_id, EvaluationStratum.PRODUCT, experiment_id, items
    )
    assert builder.frozen_bundle(history_id) == bundle
    assert store.write_manifest_calls == 2


def test_restore_is_byte_identical_across_two_arms_with_matching_parity() -> None:
    builder, store = _builder()
    ledger = InMemoryLedger()
    history_id = HistoryId.new()
    _bundle(builder, history_id=history_id)
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)

    async def restore_once() -> object:
        treatment = InMemoryTreatmentPort()
        handle = await treatment.provision(object())
        return await coordinator.restore(
            AttemptId.new(), RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )

    async def both() -> tuple[object, object]:
        return await restore_once(), await restore_once()

    manifest_a, manifest_b = asyncio.run(both())

    assert manifest_a.is_byte_identical
    assert manifest_b.is_byte_identical
    assert manifest_a.parity_hash == manifest_b.parity_hash
    assert manifest_a.restored_content_sha256 == manifest_a.bundle_content_sha256


def test_restore_replay_is_deterministic_across_many_attempts() -> None:
    builder, store = _builder()
    ledger = InMemoryLedger()
    history_id = HistoryId.new()
    _bundle(builder, history_id=history_id)
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)

    async def restore_once() -> object:
        treatment = InMemoryTreatmentPort()
        handle = await treatment.provision(object())
        return await coordinator.restore(
            AttemptId.new(), RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )

    async def many() -> list[object]:
        return [await restore_once() for _ in range(5)]

    manifests = asyncio.run(many())
    parity_hashes = {manifest.parity_hash for manifest in manifests}
    assert len(parity_hashes) == 1
    assert all(manifest.is_byte_identical for manifest in manifests)


def test_restore_blocks_on_content_mismatch_and_leaves_evidence() -> None:
    builder, store = _builder()
    ledger = InMemoryLedger()
    history_id = HistoryId.new()
    _bundle(builder, history_id=history_id)
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)

    def tamper(refs: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
        tampered = ArtifactRef.from_bytes(b"tampered-bytes")
        return (tampered, *refs[1:])

    treatment = InMemoryTreatmentPort(tamper=tamper)

    async def run() -> None:
        handle = await treatment.provision(object())
        await coordinator.restore(
            AttemptId.new(), RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )

    with pytest.raises(ControlledRestoreMismatchError):
        asyncio.run(run())


def test_restore_requires_stratum_match() -> None:
    builder, store = _builder()
    ledger = InMemoryLedger()
    history_id = HistoryId.new()
    builder.build_golden_checkpoint(
        history_id,
        ProtocolId.new(),
        EvaluationStratum.PRODUCT,
        ExperimentId.new(),
        [_item(1, b"episode-one")],
    )
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    treatment = InMemoryTreatmentPort()

    async def run() -> None:
        handle = await treatment.provision(object())
        await coordinator.restore(
            AttemptId.new(),
            RunId.new(),
            history_id,
            EvaluationStratum.DIRECT_ENGINE,
            handle,
            treatment,
        )

    with pytest.raises(ControlledHistoryViolationError):
        asyncio.run(run())


def test_probe_write_policy_disabled_blocks_any_attempted_write() -> None:
    with pytest.raises(ControlledHistoryViolationError):
        enforce_controlled_effect_boundary(
            ProbeWriteDisposition.DISABLED,
            write_attempted=True,
            write_persisted=False,
            recorded_evidence=None,
            controlled_identities=(),
        )


def test_probe_write_policy_discarded_requires_no_persistence() -> None:
    with pytest.raises(ControlledHistoryViolationError):
        enforce_controlled_effect_boundary(
            ProbeWriteDisposition.DISCARDED,
            write_attempted=True,
            write_persisted=True,
            recorded_evidence=None,
            controlled_identities=(),
        )


def test_probe_write_policy_recorded_separately_requires_evidence() -> None:
    with pytest.raises(ControlledHistoryViolationError):
        enforce_controlled_effect_boundary(
            ProbeWriteDisposition.RECORDED_SEPARATELY,
            write_attempted=True,
            write_persisted=False,
            recorded_evidence=None,
            controlled_identities=(),
        )


def test_controlled_and_dynamic_outcomes_cannot_be_pooled() -> None:
    from memrelay_eval.orchestration.history import SequenceAnalysisIdentity

    builder, store = _builder()
    ledger = InMemoryLedger()
    history_id = HistoryId.new()
    _bundle(builder, history_id=history_id)
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    controlled_identity = coordinator.analysis_identity(history_id)
    dynamic_identity = SequenceAnalysisIdentity(
        HistoryMode.DYNAMIC, EvaluationStratum.PRODUCT, "b" * 64
    )

    with pytest.raises(ControlledEstimandPoolingError):
        require_no_cross_regime_pooling((controlled_identity,), (dynamic_identity,))

    with pytest.raises(ControlledHistoryViolationError):
        enforce_controlled_effect_boundary(
            ProbeWriteDisposition.DISCARDED,
            write_attempted=False,
            write_persisted=False,
            recorded_evidence=None,
            controlled_identities=(controlled_identity,),
            dynamic_identities=(dynamic_identity,),
        )


def test_only_controlled_identity_may_aggregate() -> None:
    builder, store = _builder()
    ledger = InMemoryLedger()
    history_id_a = HistoryId.new()
    history_id_b = HistoryId.new()
    _bundle(builder, history_id=history_id_a)
    _bundle(builder, history_id=history_id_b)
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)

    identity_a = coordinator.analysis_identity(history_id_a)
    identity_b = coordinator.analysis_identity(history_id_b)

    assert require_same_controlled_analysis_identity((identity_a,)) == identity_a
    with pytest.raises(ControlledEstimandPoolingError):
        require_same_controlled_analysis_identity((identity_a, identity_b))
    with pytest.raises(ControlledEstimandPoolingError):
        require_same_controlled_analysis_identity(())


def test_fake_artifact_store_and_ledger_are_labeled_unpaid_conformance() -> None:
    builder, store = _builder()
    ledger = InMemoryLedger()
    history_id = HistoryId.new()
    _bundle(builder, history_id=history_id)
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    treatment = InMemoryTreatmentPort()

    assert store.provenance == "unpaid_conformance"
    assert store.eligible_for_paid_or_study is False
    assert ledger.provenance == "unpaid_conformance"
    assert ledger.eligible_for_paid_or_study is False
    assert treatment.provenance == "unpaid_conformance"
    assert treatment.eligible_for_paid_or_study is False

    async def run() -> None:
        handle = await treatment.provision(object())
        await coordinator.restore(
            AttemptId.new(), RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )

    asyncio.run(run())
    assert len(ledger.artifact_links) == 1
    assert ledger.artifact_links[0].purpose == "controlled_restore_manifest"


def test_controlled_history_module_never_touches_a_filesystem_path() -> None:
    """Restoration is expressed only through opaque, content-addressed ``ArtifactRef``.

    There is no path parameter anywhere in the controlled-history domain contract, so a
    path-traversal, junction, or symlink attack has no surface to target here: the only
    place a real filesystem path exists is the already hardened Story 2.2 attempt-local
    workspace root that a concrete ``TreatmentPort`` adapter restores into.
    """

    source = (
        Path(__file__).parents[3] / "src" / "memrelay_eval" / "orchestration" / "history.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("import os", "import pathlib", "from pathlib", "open("):
        assert forbidden not in source, forbidden
