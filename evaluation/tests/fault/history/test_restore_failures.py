"""Story 2.8: controlled-history restore failure and cleanup fault tests.

Covers partial restore, tampering, cross-sequence/aliasing substitution, interruption
mid-restore, and post-failure cleanup/root non-reuse.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from memrelay_eval.adapters.fakes import (
    InMemoryArtifactStore,
    InMemoryLedger,
    InMemoryTreatmentPort,
)
from memrelay_eval.domain.entities import (
    GOLDEN_SETUP_PROVENANCE,
    ArtifactRef,
    ControlledHistoryItem,
)
from memrelay_eval.domain.errors import (
    ControlledHistoryViolationError,
    ControlledRestoreMismatchError,
)
from memrelay_eval.domain.ids import AttemptId, ExperimentId, HistoryId, ProtocolId, RunId
from memrelay_eval.domain.states import EvaluationStratum
from memrelay_eval.orchestration.attempt import controlled_restore_failure_terminal
from memrelay_eval.orchestration.history import (
    ControlledHistoryBuilder,
    ControlledHistoryCoordinator,
)

_VALID_FROM = datetime(2026, 1, 1, tzinfo=UTC)


def _item(position: int, content: bytes) -> ControlledHistoryItem:
    return ControlledHistoryItem(
        position,
        ArtifactRef.from_bytes(content),
        f"actor_{position:032x}",
        "session",
        "rev-1",
        GOLDEN_SETUP_PROVENANCE,
        _VALID_FROM,
        None,
        "a" * 64,
    )


def _frozen_bundle_setup() -> tuple[
    ControlledHistoryBuilder, InMemoryArtifactStore, InMemoryLedger, HistoryId
]:
    store = InMemoryArtifactStore()
    ledger = InMemoryLedger()
    builder = ControlledHistoryBuilder(store)
    history_id = HistoryId.new()
    builder.build_golden_checkpoint(
        history_id,
        ProtocolId.new(),
        EvaluationStratum.PRODUCT,
        ExperimentId.new(),
        [_item(1, b"episode-one"), _item(2, b"episode-two"), _item(3, b"episode-three")],
    )
    return builder, store, ledger, history_id


def test_partial_restore_missing_trailing_item_blocks_exposure() -> None:
    builder, store, ledger, history_id = _frozen_bundle_setup()
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    treatment = InMemoryTreatmentPort(tamper=lambda refs: refs[:-1])

    async def run() -> None:
        handle = await treatment.provision(object())
        await coordinator.restore(
            AttemptId.new(), RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )

    with pytest.raises(ControlledRestoreMismatchError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.code == "controlled_restore_item_count_mismatch"


def test_missing_all_items_blocks_exposure() -> None:
    builder, store, ledger, history_id = _frozen_bundle_setup()
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    treatment = InMemoryTreatmentPort(tamper=lambda refs: ())

    async def run() -> None:
        handle = await treatment.provision(object())
        await coordinator.restore(
            AttemptId.new(), RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )

    with pytest.raises(ControlledRestoreMismatchError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.code == "controlled_restore_missing_all_items"


def test_extra_restored_item_blocks_exposure() -> None:
    builder, store, ledger, history_id = _frozen_bundle_setup()
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    treatment = InMemoryTreatmentPort(
        tamper=lambda refs: (*refs, ArtifactRef.from_bytes(b"unexpected-extra-episode"))
    )

    async def run() -> None:
        handle = await treatment.provision(object())
        await coordinator.restore(
            AttemptId.new(), RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )

    with pytest.raises(ControlledRestoreMismatchError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.code == "controlled_restore_item_count_mismatch"


def test_one_bit_tamper_in_middle_item_blocks_exposure() -> None:
    builder, store, ledger, history_id = _frozen_bundle_setup()
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)

    def tamper(refs: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
        tampered_middle = ArtifactRef.from_bytes(b"episode-twp")  # one-character flip
        return (refs[0], tampered_middle, refs[2])

    treatment = InMemoryTreatmentPort(tamper=tamper)

    async def run() -> None:
        handle = await treatment.provision(object())
        await coordinator.restore(
            AttemptId.new(), RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )

    with pytest.raises(ControlledRestoreMismatchError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.code == "controlled_restore_content_mismatch"


def test_reordered_aliasing_of_identical_length_items_is_detected() -> None:
    """Swapping two positions is a content mismatch unless the swapped bytes are equal."""

    builder, store, ledger, history_id = _frozen_bundle_setup()
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)

    def alias_swap(refs: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
        return (refs[1], refs[0], refs[2])

    treatment = InMemoryTreatmentPort(tamper=alias_swap)

    async def run() -> None:
        handle = await treatment.provision(object())
        await coordinator.restore(
            AttemptId.new(), RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )

    with pytest.raises(ControlledRestoreMismatchError):
        asyncio.run(run())


def test_cross_sequence_history_substitution_is_rejected() -> None:
    """Restoring against an unfrozen/foreign history_id is rejected before any I/O."""

    builder, store, ledger, _ = _frozen_bundle_setup()
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    foreign_history_id = HistoryId.new()
    treatment = InMemoryTreatmentPort()

    async def run() -> None:
        handle = await treatment.provision(object())
        await coordinator.restore(
            AttemptId.new(),
            RunId.new(),
            foreign_history_id,
            EvaluationStratum.PRODUCT,
            handle,
            treatment,
        )

    with pytest.raises(ControlledHistoryViolationError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.code == "controlled_history_not_frozen"


def test_attempt_id_aliasing_cannot_replay_a_second_restore() -> None:
    """The same attempt_id can never consume a second, possibly-different, restore."""

    builder, store, ledger, history_id = _frozen_bundle_setup()
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    attempt_id = AttemptId.new()
    treatment = InMemoryTreatmentPort()

    async def run() -> None:
        handle = await treatment.provision(object())
        await coordinator.restore(
            attempt_id, RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )
        await coordinator.restore(
            attempt_id, RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )

    with pytest.raises(ControlledHistoryViolationError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.code == "controlled_restore_already_consumed"


def test_interruption_mid_restore_is_typed_pre_exposure_and_blocks_same_attempt_retry() -> None:
    """A transport failure mid-restore is typed, pre-exposure, and never silently retried.

    The failed attempt_id is permanently consumed (no root reuse); a retry must use a
    brand-new attempt_id (per AD-11/AD-18), which this coordinator will happily restore
    into a fresh handle because verification always re-runs from scratch.
    """

    builder, store, ledger, history_id = _frozen_bundle_setup()
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    crashing_treatment = InMemoryTreatmentPort(fail_on_restore=True)
    attempt_id = AttemptId.new()
    run_id = RunId.new()

    async def crash() -> None:
        handle = await crashing_treatment.provision(object())
        await coordinator.restore(
            attempt_id, run_id, history_id, EvaluationStratum.PRODUCT, handle, crashing_treatment
        )

    with pytest.raises(RuntimeError):
        asyncio.run(crash())

    # The immutable failure evidence a caller would record for the blocked attempt.
    evidence = ArtifactRef.from_bytes(b"controlled-restore-transport-failure-evidence")
    terminal = controlled_restore_failure_terminal(
        attempt_id, run_id, "controlled_restore_transport_failed", (evidence,)
    )
    assert terminal.classification.value == "infrastructure_failed_pre_exposure"
    assert terminal.evidence_refs == (evidence,)

    # Even a healthy treatment adapter cannot reuse the already-consumed attempt_id.
    healthy_treatment = InMemoryTreatmentPort()

    async def retry_same_attempt_healthy() -> None:
        handle = await healthy_treatment.provision(object())
        await coordinator.restore(
            attempt_id, run_id, history_id, EvaluationStratum.PRODUCT, handle, healthy_treatment
        )

    with pytest.raises(ControlledHistoryViolationError) as excinfo:
        asyncio.run(retry_same_attempt_healthy())
    assert excinfo.value.code == "controlled_restore_already_consumed"


def test_successful_retry_uses_a_fresh_attempt_id_and_root() -> None:
    """A real retry after a pre-exposure failure gets a brand-new attempt_id/root."""

    builder, store, ledger, history_id = _frozen_bundle_setup()
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    crashing_treatment = InMemoryTreatmentPort(fail_on_restore=True)
    parent_attempt_id = AttemptId.new()
    run_id = RunId.new()

    async def crash() -> None:
        handle = await crashing_treatment.provision(object())
        await coordinator.restore(
            parent_attempt_id,
            run_id,
            history_id,
            EvaluationStratum.PRODUCT,
            handle,
            crashing_treatment,
        )

    with pytest.raises(RuntimeError):
        asyncio.run(crash())

    retry_attempt_id = AttemptId.new()
    healthy_treatment = InMemoryTreatmentPort()

    async def retry() -> object:
        handle = await healthy_treatment.provision(object())
        return await coordinator.restore(
            retry_attempt_id,
            run_id,
            history_id,
            EvaluationStratum.PRODUCT,
            handle,
            healthy_treatment,
        )

    manifest = asyncio.run(retry())
    assert manifest.is_byte_identical
    assert manifest.attempt_id == retry_attempt_id


def test_cleanup_closes_treatment_handle_without_disturbing_restore_evidence() -> None:
    builder, store, ledger, history_id = _frozen_bundle_setup()
    coordinator = ControlledHistoryCoordinator(builder, store, ledger)
    treatment = InMemoryTreatmentPort()

    async def run() -> object:
        handle = await treatment.provision(object())
        manifest = await coordinator.restore(
            AttemptId.new(), RunId.new(), history_id, EvaluationStratum.PRODUCT, handle, treatment
        )
        await treatment.close(handle)
        return manifest

    manifest = asyncio.run(run())
    assert manifest.is_byte_identical
    assert treatment.closed_handles == 1
    # Cleanup never removes the append-only restore evidence already on the ledger.
    assert len(ledger.artifact_links) == 1
