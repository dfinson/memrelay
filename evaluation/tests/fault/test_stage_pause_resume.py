from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from memrelay_eval.domain.errors import StageAuthorizationError, StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageAuthorizationId, StageId
from memrelay_eval.domain.policies import STAGE_ENTRY_LOCK_FIELDS
from memrelay_eval.domain.states import StageKind, StageState
from memrelay_eval.orchestration.stages import (
    StageAuthorization,
    StageBundleStore,
    StageEntryBundle,
    StageExitBundle,
    StageUnit,
    authorize_stage_entry,
    load_authorization,
    load_entry_bundle,
    load_exit_bundle,
    plan_stage_resume,
)

HASH_A = "a" * 64
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _entry_locks(preceding: str) -> dict[str, str]:
    locks = dict.fromkeys(STAGE_ENTRY_LOCK_FIELDS, HASH_A)
    locks["preceding_exit_sha256"] = preceding
    return locks


def _accepted_predecessor() -> StageExitBundle:
    return StageExitBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.CONFORMANCE,
        protocol_id=ProtocolId.new(),
        entry_bundle_sha256="b" * 64,
        preceding_exit_sha256="c" * 64,
        status=StageState.ACCEPTED,
        reconciliation_sha256="d" * 64,
        inclusion_decision_sha256="e" * 64,
        authorization_id=StageAuthorizationId.new(),
    )


def _bundles() -> tuple[StageEntryBundle, StageExitBundle, StageAuthorization]:
    predecessor = _accepted_predecessor()
    stage_id = StageId.new()
    protocol_id = ProtocolId.new()
    entry = StageEntryBundle(
        stage_id=stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=protocol_id,
        predecessor_stage_kind=StageKind.CONFORMANCE,
        locks=_entry_locks(predecessor.digest),
    )
    authorization = StageAuthorization(
        authorization_id=StageAuthorizationId.new(),
        stage_id=stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=protocol_id,
        entry_bundle_sha256=entry.digest,
        envelope_sha256=entry.envelope_sha256,
        authorizer_id="operator-1",
        authorizer_role="operator",
        valid_from=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(hours=1),
        paid_execution=True,
    )
    return entry, predecessor, authorization


def _authorize(entry, predecessor, authorization, *, now: datetime = NOW) -> None:
    authorize_stage_entry(
        stage_kind=StageKind.INTEGRATION,
        entry_bundle=entry,
        predecessor_exit=predecessor,
        authorization=authorization,
        now=now,
    )


def _resume_kwargs(authorization: StageAuthorization) -> dict[str, object]:
    return {
        "authorization": authorization,
        "now": NOW,
        "locks_verified": True,
        "receipts_consistent": True,
        "ledger_cas_consistent": True,
        "circuit_breaker_open": False,
    }


def test_valid_sealed_bundles_admit_entry() -> None:
    entry, predecessor, authorization = _bundles()
    _authorize(entry, predecessor, authorization)


def test_missing_predecessor_fails_closed() -> None:
    entry, _predecessor, authorization = _bundles()
    with pytest.raises(StageControlError) as failure:
        authorize_stage_entry(
            stage_kind=StageKind.INTEGRATION,
            entry_bundle=entry,
            predecessor_exit=None,
            authorization=authorization,
            now=NOW,
        )
    assert failure.value.code == "missing_predecessor_exit"


def test_incomplete_predecessor_is_refused() -> None:
    entry, _predecessor, authorization = _bundles()
    # A rejected exit is never accepted-and-complete.
    incomplete = StageExitBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.CONFORMANCE,
        protocol_id=ProtocolId.new(),
        entry_bundle_sha256="b" * 64,
        preceding_exit_sha256="c" * 64,
        status=StageState.REJECTED,
        reconciliation_sha256="d" * 64,
        inclusion_decision_sha256="e" * 64,
        authorization_id=StageAuthorizationId.new(),
    )
    with pytest.raises(StageControlError) as failure:
        authorize_stage_entry(
            stage_kind=StageKind.INTEGRATION,
            entry_bundle=entry,
            predecessor_exit=incomplete,
            authorization=authorization,
            now=NOW,
        )
    assert failure.value.code == "predecessor_exit_rejected"


@pytest.mark.parametrize("flip_byte", (b"a", b"z"))
def test_corrupt_predecessor_bytes_fail_closed(flip_byte: bytes) -> None:
    _entry, predecessor, _authorization = _bundles()
    tampered = bytearray(predecessor.bytes())
    tampered[10:11] = flip_byte
    with pytest.raises(StageControlError) as failure:
        load_exit_bundle(bytes(tampered))
    assert failure.value.code == "predecessor_exit_corrupt"


def test_corrupt_entry_bundle_bytes_fail_closed() -> None:
    entry, _predecessor, _authorization = _bundles()
    tampered = entry.bytes().replace(b'"integration"', b'"pilot"')
    with pytest.raises(StageControlError) as failure:
        load_entry_bundle(tampered)
    assert failure.value.code == "stage_entry_bundle_corrupt"


def test_corrupt_authorization_bytes_fail_closed() -> None:
    _entry, _predecessor, authorization = _bundles()
    tampered = authorization.bytes().replace(b'"operator"', b'"process"')
    with pytest.raises(StageAuthorizationError) as failure:
        load_authorization(tampered)
    assert failure.value.code == "authorization_corrupt"


def test_stale_authorization_fails_closed_after_expiry() -> None:
    entry, predecessor, authorization = _bundles()
    with pytest.raises(StageAuthorizationError) as failure:
        _authorize(entry, predecessor, authorization, now=NOW + timedelta(hours=2))
    assert failure.value.code == "stale_authorization"


def test_link_mismatch_between_entry_and_predecessor_fails_closed() -> None:
    _entry, predecessor, _authorization = _bundles()
    # A fresh entry whose lock references the wrong preceding-exit digest.
    stage_id = StageId.new()
    protocol_id = ProtocolId.new()
    mislinked_entry = StageEntryBundle(
        stage_id=stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=protocol_id,
        predecessor_stage_kind=StageKind.CONFORMANCE,
        locks=_entry_locks("f" * 64),
    )
    authorization = StageAuthorization(
        authorization_id=StageAuthorizationId.new(),
        stage_id=stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=protocol_id,
        entry_bundle_sha256=mislinked_entry.digest,
        envelope_sha256=mislinked_entry.envelope_sha256,
        authorizer_id="operator-1",
        authorizer_role="operator",
        valid_from=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(hours=1),
        paid_execution=True,
    )
    with pytest.raises(StageControlError) as failure:
        authorize_stage_entry(
            stage_kind=StageKind.INTEGRATION,
            entry_bundle=mislinked_entry,
            predecessor_exit=predecessor,
            authorization=authorization,
            now=NOW,
        )
    assert failure.value.code == "predecessor_exit_link_mismatch"


def test_bundle_store_reseal_is_idempotent(tmp_path: Path) -> None:
    entry, _predecessor, _authorization = _bundles()
    store = StageBundleStore(tmp_path)

    first_path, first_outcome = store.seal_entry(entry)
    second_path, second_outcome = store.seal_entry(entry)

    assert first_outcome == "sealed"
    assert second_outcome == "reused"
    assert first_path == second_path
    assert first_path.read_bytes() == entry.bytes()


def test_bundle_store_reseal_conflict_fails_closed(tmp_path: Path) -> None:
    entry, _predecessor, _authorization = _bundles()
    store = StageBundleStore(tmp_path)
    path, _outcome = store.seal_entry(entry)

    # Simulate a post-seal mutation attempt at the same stage/protocol identity.
    mutated = bytearray(entry.bytes())
    mutated[20] = mutated[20] ^ 0x01
    path.write_bytes(bytes(mutated))
    with pytest.raises(StageControlError) as failure:
        store.seal_entry(entry)
    assert failure.value.code == "stage_bundle_mutation"


def test_bundle_store_concurrent_differing_writer_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A concurrent writer publishes *different* bytes for the same logical identity
    # after our existence check but before we publish. The exclusive publish must
    # observe the winner and fail closed instead of silently clobbering it.
    import memrelay_eval.orchestration.stages as stages_module

    entry, _predecessor, _authorization = _bundles()
    store = StageBundleStore(tmp_path)
    real_link = os.link

    def racing_link(src: object, dst: object, *args: object, **kwargs: object) -> None:
        Path(dst).write_bytes(b"conflicting-content")  # losing race with different bytes
        return real_link(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(stages_module.os, "link", racing_link)
    with pytest.raises(StageControlError) as failure:
        store.seal_entry(entry)
    assert failure.value.code == "stage_bundle_mutation"


def test_bundle_store_concurrent_identical_writer_reuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A concurrent writer publishes *identical* bytes; the race must resolve to a
    # reuse (data preserved) rather than a spurious conflict.
    import memrelay_eval.orchestration.stages as stages_module

    entry, _predecessor, _authorization = _bundles()
    store = StageBundleStore(tmp_path)
    real_link = os.link

    def racing_link(src: object, dst: object, *args: object, **kwargs: object) -> None:
        Path(dst).write_bytes(Path(src).read_bytes())  # identical content wins the race
        return real_link(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(stages_module.os, "link", racing_link)
    path, outcome = store.seal_entry(entry)
    assert outcome == "reused"
    assert path.read_bytes() == entry.bytes()


def test_bundle_store_threaded_identical_writers_publish_once_and_reuse(tmp_path: Path) -> None:
    entry, _predecessor, _authorization = _bundles()
    store = StageBundleStore(tmp_path)
    barrier = Barrier(8)

    def seal() -> tuple[Path, str]:
        barrier.wait()
        return store.seal_entry(entry)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: seal(), range(8)))

    paths, outcomes = zip(*results, strict=True)
    assert len(set(paths)) == 1
    assert outcomes.count("sealed") == 1
    assert outcomes.count("reused") == 7
    assert paths[0].read_bytes() == entry.bytes()
    assert not list(paths[0].parent.glob(".*.staged"))


def test_bundle_store_threaded_differing_writers_fail_closed(tmp_path: Path) -> None:
    entry, _predecessor, _authorization = _bundles()
    locks = dict(entry.locks)
    locks["model_lock_sha256"] = "b" * 64
    conflicting = StageEntryBundle(
        stage_id=entry.stage_id,
        stage_kind=entry.stage_kind,
        protocol_id=entry.protocol_id,
        predecessor_stage_kind=entry.predecessor_stage_kind,
        locks=locks,
    )
    store = StageBundleStore(tmp_path)
    barrier = Barrier(2)

    def seal(bundle: StageEntryBundle) -> tuple[Path, str]:
        barrier.wait()
        return store.seal_entry(bundle)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(seal, bundle) for bundle in (entry, conflicting)]
        results = [
            future.result() if not future.exception() else future.exception() for future in futures
        ]

    assert sum(isinstance(result, StageControlError) for result in results) == 1
    failure = next(result for result in results if isinstance(result, StageControlError))
    assert failure.code == "stage_bundle_mutation"
    success = next(result for result in results if not isinstance(result, StageControlError))
    assert isinstance(success, tuple)
    assert success[1] == "sealed"
    assert success[0].read_bytes() in {entry.bytes(), conflicting.bytes()}
    assert not list(success[0].parent.glob(".*.staged"))


def test_entry_bundle_with_nan_token_fails_closed() -> None:
    # json.loads accepts the bare NaN token, but the canonical hashing service
    # rejects it. The loader must fail closed with the typed code instead of
    # letting CanonicalizationError escape and crash the CLI.
    with pytest.raises(StageControlError) as failure:
        load_entry_bundle(b'{"artifact_type": "stage_entry_bundle", "value": NaN}')
    assert failure.value.code == "stage_entry_bundle_corrupt"


def test_entry_bundle_locks_are_immutable_after_seal() -> None:
    # A sealed entry bundle must reject in-place lock mutation so the lazily
    # computed digest/envelope can never be silently redirected.
    entry, _predecessor, _authorization = _bundles()
    with pytest.raises(TypeError):
        entry.locks["runtime_lock_sha256"] = "0" * 64  # type: ignore[index]


def test_resume_returns_only_unfinished_units() -> None:
    _entry, _predecessor, authorization = _bundles()
    units = (
        StageUnit(unit_id="u1", terminal=True),
        StageUnit(unit_id="u2", terminal=False),
        StageUnit(unit_id="u3", terminal=False),
    )
    resumable = plan_stage_resume(units, **_resume_kwargs(authorization))
    assert resumable == ("u2", "u3")


def test_duplicate_resume_is_stable() -> None:
    _entry, _predecessor, authorization = _bundles()
    units = (StageUnit(unit_id="u1", terminal=False),)
    kwargs = _resume_kwargs(authorization)
    assert plan_stage_resume(units, **kwargs) == plan_stage_resume(units, **kwargs)


@pytest.mark.parametrize(
    ("override", "code"),
    (
        ({"circuit_breaker_open": True}, "resume_circuit_breaker_open"),
        ({"locks_verified": False}, "resume_lock_drift"),
        ({"receipts_consistent": False}, "resume_receipt_conflict"),
        ({"ledger_cas_consistent": False}, "resume_ledger_cas_conflict"),
    ),
)
def test_resume_precondition_failures_fail_closed(override: dict[str, object], code: str) -> None:
    _entry, _predecessor, authorization = _bundles()
    units = (StageUnit(unit_id="u1", terminal=False),)
    kwargs = _resume_kwargs(authorization) | override
    with pytest.raises(StageControlError) as failure:
        plan_stage_resume(units, **kwargs)
    assert failure.value.code == code


def test_resume_refuses_after_authorization_revocation() -> None:
    _entry, _predecessor, authorization = _bundles()
    units = (StageUnit(unit_id="u1", terminal=False),)
    kwargs = _resume_kwargs(authorization) | {"now": NOW + timedelta(hours=5)}
    with pytest.raises(StageAuthorizationError) as failure:
        plan_stage_resume(units, **kwargs)
    assert failure.value.code == "stale_authorization"
