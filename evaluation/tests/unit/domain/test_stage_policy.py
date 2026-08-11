from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from memrelay_eval.domain.errors import StageAuthorizationError, StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageAuthorizationId, StageId
from memrelay_eval.domain.policies import (
    ENROLLABLE_STAGES,
    STAGE_ENTRY_LOCK_FIELDS,
    require_independent_authorizer_role,
    require_stage_entry_locks,
    require_stage_predecessor,
    validate_stage_transition,
)
from memrelay_eval.domain.states import StageKind, StageState
from memrelay_eval.orchestration.limits import stage_envelope_digest, stage_headroom_status
from memrelay_eval.orchestration.stages import (
    StageAuthorization,
    StageEntryBundle,
    StageExitBundle,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

LEGAL_TRANSITIONS = (
    (StageState.PLANNED, StageState.AUTHORIZED),
    (StageState.PLANNED, StageState.REJECTED),
    (StageState.AUTHORIZED, StageState.RUNNING),
    (StageState.AUTHORIZED, StageState.REJECTED),
    (StageState.RUNNING, StageState.PAUSED),
    (StageState.RUNNING, StageState.CLOSING),
    (StageState.RUNNING, StageState.REJECTED),
    (StageState.PAUSED, StageState.RUNNING),
    (StageState.PAUSED, StageState.CLOSING),
    (StageState.CLOSING, StageState.ACCEPTED),
    (StageState.CLOSING, StageState.REJECTED),
)

ILLEGAL_TRANSITIONS = (
    # Process completion never advances a stage: no planned->running edge.
    (StageState.PLANNED, StageState.RUNNING),
    (StageState.PLANNED, StageState.ACCEPTED),
    # A rejected stage is terminal and can never be re-entered.
    (StageState.REJECTED, StageState.PLANNED),
    (StageState.REJECTED, StageState.AUTHORIZED),
    (StageState.ACCEPTED, StageState.RUNNING),
    (StageState.ACCEPTED, StageState.CLOSING),
    (StageState.AUTHORIZED, StageState.ACCEPTED),
    (StageState.RUNNING, StageState.ACCEPTED),
)

REQUIRED_PREDECESSORS = (
    (StageKind.INTEGRATION, StageKind.CONFORMANCE),
    (StageKind.PILOT, StageKind.INTEGRATION),
    (StageKind.PRIMARY, StageKind.PILOT),
    (StageKind.SECONDARY, StageKind.PRIMARY),
    (StageKind.CROSS_REPOSITORY, StageKind.PRIMARY),
)


def _entry_locks(preceding: str = HASH_A) -> dict[str, str]:
    locks = dict.fromkeys(STAGE_ENTRY_LOCK_FIELDS, HASH_A)
    locks["preceding_exit_sha256"] = preceding
    return locks


def _entry_bundle(
    *,
    stage_id: StageId,
    protocol_id: ProtocolId,
    stage_kind: StageKind = StageKind.INTEGRATION,
    predecessor: StageKind = StageKind.CONFORMANCE,
    preceding: str = HASH_A,
) -> StageEntryBundle:
    return StageEntryBundle(
        stage_id=stage_id,
        stage_kind=stage_kind,
        protocol_id=protocol_id,
        predecessor_stage_kind=predecessor,
        locks=_entry_locks(preceding),
    )


def _authorization(
    *,
    stage_id: StageId,
    protocol_id: ProtocolId,
    entry_digest: str,
    envelope: str,
    role: str = "operator",
    valid_from: datetime = NOW - timedelta(hours=1),
    valid_until: datetime = NOW + timedelta(hours=1),
) -> StageAuthorization:
    return StageAuthorization(
        authorization_id=StageAuthorizationId.new(),
        stage_id=stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=protocol_id,
        entry_bundle_sha256=entry_digest,
        envelope_sha256=envelope,
        authorizer_id="operator-1",
        authorizer_role=role,
        valid_from=valid_from,
        valid_until=valid_until,
        paid_execution=True,
    )


@pytest.mark.parametrize(("previous", "next_state"), LEGAL_TRANSITIONS)
def test_legal_stage_transitions_are_accepted(previous: StageState, next_state: StageState) -> None:
    validate_stage_transition(previous, next_state)


@pytest.mark.parametrize(("previous", "next_state"), ILLEGAL_TRANSITIONS)
def test_illegal_stage_transitions_fail_closed(
    previous: StageState, next_state: StageState
) -> None:
    with pytest.raises(StageControlError) as failure:
        validate_stage_transition(previous, next_state)
    assert failure.value.code == "invalid_stage_transition"


@pytest.mark.parametrize(("stage", "predecessor"), REQUIRED_PREDECESSORS)
def test_required_predecessor_is_enforced(stage: StageKind, predecessor: StageKind) -> None:
    assert require_stage_predecessor(stage, predecessor) is predecessor


@pytest.mark.parametrize("stage", sorted(ENROLLABLE_STAGES, key=lambda kind: kind.value))
def test_skipped_predecessor_is_refused(stage: StageKind) -> None:
    with pytest.raises(StageControlError) as failure:
        require_stage_predecessor(stage, StageKind.BOOTSTRAP)
    assert failure.value.code == "stage_skipped"


def test_entry_locks_require_exactly_the_twelve_frozen_fields() -> None:
    assert len(STAGE_ENTRY_LOCK_FIELDS) == 12
    require_stage_entry_locks(_entry_locks())


def test_entry_locks_reject_missing_field() -> None:
    locks = _entry_locks()
    del locks["telemetry_sha256"]
    with pytest.raises(StageControlError) as failure:
        require_stage_entry_locks(locks)
    assert failure.value.code == "stage_bundle_incomplete"


def test_entry_locks_reject_extra_field() -> None:
    locks = _entry_locks()
    locks["unexpected_sha256"] = HASH_A
    with pytest.raises(StageControlError) as failure:
        require_stage_entry_locks(locks)
    assert failure.value.code == "stage_bundle_incomplete"


def test_entry_locks_reject_non_sha256_value() -> None:
    locks = _entry_locks()
    locks["catalog_sha256"] = "NOTAHASH"
    with pytest.raises(StageControlError) as failure:
        require_stage_entry_locks(locks)
    assert failure.value.code == "stage_bundle_hash_invalid"


@pytest.mark.parametrize("role", ("operator", "scheduler"))
def test_independent_authorizer_roles_are_accepted(role: str) -> None:
    assert require_independent_authorizer_role(role) == role


@pytest.mark.parametrize("role", ("process", "self", "reconciler", "", None, 3))
def test_self_authorization_is_denied(role: object) -> None:
    with pytest.raises(StageAuthorizationError) as failure:
        require_independent_authorizer_role(role)
    assert failure.value.code == "self_authorization_denied"


def test_entry_bundle_digest_is_stable_across_lock_ordering() -> None:
    stage_id = StageId.new()
    protocol_id = ProtocolId.new()
    ordered = _entry_bundle(stage_id=stage_id, protocol_id=protocol_id)
    shuffled_locks = dict(reversed(list(_entry_locks().items())))
    shuffled = StageEntryBundle(
        stage_id=stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=protocol_id,
        predecessor_stage_kind=StageKind.CONFORMANCE,
        locks=shuffled_locks,
    )
    assert ordered.digest == shuffled.digest
    assert ordered.bytes() == shuffled.bytes()


def test_entry_bundle_digest_changes_when_any_lock_changes() -> None:
    stage_id = StageId.new()
    protocol_id = ProtocolId.new()
    base = _entry_bundle(stage_id=stage_id, protocol_id=protocol_id)
    mutated_locks = _entry_locks()
    mutated_locks["protocol_sha256"] = HASH_B
    mutated = StageEntryBundle(
        stage_id=stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=protocol_id,
        predecessor_stage_kind=StageKind.CONFORMANCE,
        locks=mutated_locks,
    )
    assert base.digest != mutated.digest


def test_envelope_digest_binds_price_and_limits() -> None:
    first = stage_envelope_digest(HASH_A, HASH_B)
    assert first == stage_envelope_digest(HASH_A, HASH_B)
    assert first != stage_envelope_digest(HASH_B, HASH_A)


def test_exit_bundle_rejects_non_terminal_status() -> None:
    with pytest.raises(StageControlError) as failure:
        StageExitBundle(
            stage_id=StageId.new(),
            stage_kind=StageKind.CONFORMANCE,
            protocol_id=ProtocolId.new(),
            entry_bundle_sha256=HASH_A,
            preceding_exit_sha256=HASH_B,
            status=StageState.RUNNING,
            reconciliation_sha256=HASH_A,
            inclusion_decision_sha256=HASH_B,
            authorization_id=StageAuthorizationId.new(),
        )
    assert failure.value.code == "stage_exit_status_invalid"


def test_authorization_expiry_is_half_open() -> None:
    stage_id = StageId.new()
    protocol_id = ProtocolId.new()
    entry = _entry_bundle(stage_id=stage_id, protocol_id=protocol_id)
    authorization = _authorization(
        stage_id=stage_id,
        protocol_id=protocol_id,
        entry_digest=entry.digest,
        envelope=entry.envelope_sha256,
        valid_from=NOW,
        valid_until=NOW + timedelta(hours=1),
    )
    assert authorization.is_current(NOW) is True
    assert authorization.is_current(NOW - timedelta(seconds=1)) is False
    assert authorization.is_current(NOW + timedelta(hours=1)) is False


def test_authorization_rejects_inverted_validity_window() -> None:
    stage_id = StageId.new()
    protocol_id = ProtocolId.new()
    entry = _entry_bundle(stage_id=stage_id, protocol_id=protocol_id)
    with pytest.raises(StageAuthorizationError) as failure:
        _authorization(
            stage_id=stage_id,
            protocol_id=protocol_id,
            entry_digest=entry.digest,
            envelope=entry.envelope_sha256,
            valid_from=NOW + timedelta(hours=1),
            valid_until=NOW,
        )
    assert failure.value.code == "authorization_validity_invalid"


def test_headroom_reports_exhaustion_without_revealing_treatment() -> None:
    status = stage_headroom_status(
        {"framework_tokens": 10, "usd": 5}, {"framework_tokens": 10, "usd": 20}
    )
    assert status["exhausted"] is True
    dimensions = status["dimensions"]
    assert dimensions["framework_tokens"]["remaining"] == 0
    assert dimensions["framework_tokens"]["exhausted"] is True
    assert dimensions["usd"]["remaining"] == 15


def test_headroom_rejects_dimension_mismatch() -> None:
    with pytest.raises(StageControlError) as failure:
        stage_headroom_status({"usd": 1}, {"framework_tokens": 1})
    assert failure.value.code == "stage_headroom_dimension_mismatch"
