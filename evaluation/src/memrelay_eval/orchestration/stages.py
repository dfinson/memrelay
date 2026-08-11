"""Fail-closed evaluator stage boundary checks."""

from __future__ import annotations

import os
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from memrelay_eval.canonical import (
    CanonicalizationError,
    attach_digest,
    canonical_bytes,
    canonical_digest,
    verify_digest,
)
from memrelay_eval.domain.engine import (
    FrameworkConfiguration,
    StratumAuthority,
    require_distinct_stratum_authorities,
    require_framework_configuration_parity,
)
from memrelay_eval.domain.entities import (
    ArtifactRef,
    ControlledAnalysisIdentity,
    NativeModelCatalog,
    ProductIdentityChain,
    RuntimeIdentity,
)
from memrelay_eval.domain.errors import (
    ConformancePauseError,
    StageAuthorizationError,
    StageControlError,
)
from memrelay_eval.domain.governance import (
    EvaluationStage,
    RepositoryAccessRequest,
    RevocationState,
)
from memrelay_eval.domain.ids import (
    AuthorizationId,
    AuthorizationVersionId,
    GovernanceRequestId,
    PolicyVersionId,
    PrincipalId,
    ProtocolId,
    PurposeId,
    PurposeVersionId,
    RepositoryId,
    StageAuthorizationId,
    StageId,
)
from memrelay_eval.domain.policies import (
    enforce_probe_write_disposition,
    require_independent_authorizer_role,
    require_single_product_stratum,
    require_stage_entry_locks,
    require_stage_predecessor,
)
from memrelay_eval.domain.states import ProbeWriteDisposition, StageKind, StageState
from memrelay_eval.orchestration.control import CrossRepositoryAdmissionController
from memrelay_eval.orchestration.history import (
    SequenceAnalysisIdentity,
    require_no_cross_regime_pooling,
    require_same_controlled_analysis_identity,
)
from memrelay_eval.orchestration.limits import stage_envelope_digest


def refuse_cross_repository_stage() -> None:
    """Refuse the unavailable v1 stage before any repository identity is resolved."""

    now = datetime.now(UTC)
    repository_id = RepositoryId.new()
    request = RepositoryAccessRequest(
        request_id=GovernanceRequestId.new(),
        task_repository_id=repository_id,
        requested_repository_id=repository_id,
        principal_id=PrincipalId.new(),
        authorization_id=AuthorizationId.new(),
        authorization_version=AuthorizationVersionId.new(),
        purpose_id=PurposeId.new(),
        purpose_version=PurposeVersionId.new(),
        policy_version=PolicyVersionId.new(),
        valid_from=now,
        valid_until=now + timedelta(days=365),
        revocation_state=RevocationState.ACTIVE,
        stage=EvaluationStage.CROSS_REPOSITORY,
    )
    CrossRepositoryAdmissionController().authorize_at_entry(request, now)


def verify_direct_engine_stage(
    product_authority: StratumAuthority,
    engine_authority: StratumAuthority,
    product_framework: FrameworkConfiguration,
    engine_framework: FrameworkConfiguration,
) -> str:
    """Gate engine provisioning on separate identities and equal framework settings."""

    require_distinct_stratum_authorities(product_authority, engine_authority)
    return require_framework_configuration_parity(product_framework, engine_framework)


def verify_stage_locks(
    runtime_lock: Mapping[str, object],
    model_lock: Mapping[str, object],
    current_runtime: RuntimeIdentity,
    current_catalog: NativeModelCatalog,
) -> None:
    """Raise a typed pause for any runtime, catalog, or selected-model drift."""

    expected_runtime = runtime_lock.get("runtime")
    if not isinstance(expected_runtime, Mapping):
        raise ConformancePauseError("runtime_lock_invalid", "runtime lock has no runtime identity")
    for key, actual in {
        "sdk_version": current_runtime.sdk_version,
        "wheel_filename": current_runtime.wheel_filename,
        "wheel_sha256": current_runtime.wheel_sha256,
        "runtime_version": current_runtime.runtime_version,
        "runtime_sha256": current_runtime.runtime_sha256,
        "transport": current_runtime.transport,
        "auth_mode": current_runtime.auth_mode,
        "subscription_identity_sha256": current_runtime.subscription_identity_sha256,
    }.items():
        if expected_runtime.get(key) != actual:
            raise ConformancePauseError("runtime_drift", f"locked runtime field changed: {key}")
    if model_lock.get("runtime_lock_sha256") != runtime_lock.get("lock_sha256"):
        raise ConformancePauseError(
            "runtime_model_link_drift", "model lock is not linked to runtime lock"
        )
    if model_lock.get("catalog_raw_sha256") != current_catalog.raw_sha256:
        raise ConformancePauseError("catalog_drift", "native model catalog bytes changed")
    if model_lock.get("catalog_projection_sha256") != current_catalog.projection_sha256:
        raise ConformancePauseError("catalog_projection_drift", "native model capabilities changed")
    catalog_by_id = {model.native_id: model for model in current_catalog.models}
    selected = model_lock.get("selected_models")
    if not isinstance(selected, list):
        raise ConformancePauseError("model_lock_invalid", "model lock has no selected models")
    for pin in selected:
        if not isinstance(pin, Mapping) or not isinstance(pin.get("native_id"), str):
            raise ConformancePauseError("model_lock_invalid", "selected model pin is malformed")
        current = catalog_by_id.get(pin["native_id"])
        if current is None:
            raise ConformancePauseError("model_unavailable", "locked native model disappeared")
        for key, actual in {
            "family": current.family,
            "capabilities": dict(current.capabilities),
            "reasoning_effort": current.reasoning_effort,
            "context_tier": current.context_tier,
        }.items():
            if pin.get(key) != actual:
                raise ConformancePauseError(
                    "model_capability_drift", f"locked model field changed: {key}"
                )


def require_product_stratum_aggregation(chains: tuple[ProductIdentityChain, ...]) -> None:
    """Apply the stratum guard at the orchestration aggregation entry point."""

    require_single_product_stratum(chains)


def enforce_controlled_effect_boundary(
    probe_disposition: ProbeWriteDisposition,
    *,
    write_attempted: bool,
    write_persisted: bool,
    recorded_evidence: ArtifactRef | None,
    controlled_identities: Sequence[ControlledAnalysisIdentity],
    dynamic_identities: Sequence[SequenceAnalysisIdentity] = (),
) -> ControlledAnalysisIdentity:
    """Enforce Story 2.8 AC3 in one place: probe writes, estimands, and non-pooling.

    Only controlled-effect estimands are permitted here; any dynamic identity present
    alongside a controlled one means the query is combining history modes and is
    rejected before any aggregation runs.
    """

    enforce_probe_write_disposition(
        probe_disposition,
        write_attempted=write_attempted,
        write_persisted=write_persisted,
        recorded_evidence=recorded_evidence,
    )
    require_no_cross_regime_pooling(controlled_identities, dynamic_identities)
    return require_same_controlled_analysis_identity(controlled_identities)


STAGE_BUNDLE_SCHEMA_VERSION = "1.0.0"


def _stage_utc(value: datetime, field_name: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StageControlError("stage_timestamp_not_utc", (field_name,))
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _require_sha256(value: object, code: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise StageControlError(code, (field_name,))
    return value


@dataclass(frozen=True, slots=True)
class StageEntryBundle:
    """Immutable seal binding every frozen input a stage entry depends on.

    Any post-seal change to a bound hash yields a different digest, and the store
    refuses to overwrite a sealed entry for the same stage and protocol identity.
    Legitimate change therefore requires a new protocol id or a new stage id.
    """

    stage_id: StageId
    stage_kind: StageKind
    protocol_id: ProtocolId
    predecessor_stage_kind: StageKind
    locks: Mapping[str, str]

    def __post_init__(self) -> None:
        require_stage_entry_locks(self.locks)
        require_stage_predecessor(self.stage_kind, self.predecessor_stage_kind)
        object.__setattr__(self, "locks", types.MappingProxyType(dict(sorted(self.locks.items()))))

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": STAGE_BUNDLE_SCHEMA_VERSION,
            "artifact_type": "stage_entry_bundle",
            "stage_id": str(self.stage_id),
            "stage_kind": self.stage_kind.value,
            "protocol_id": str(self.protocol_id),
            "predecessor_stage_kind": self.predecessor_stage_kind.value,
            "locks": {key: str(value) for key, value in self.locks.items()},
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    @property
    def envelope_sha256(self) -> str:
        return stage_envelope_digest(self.locks["price_table_sha256"], self.locks["limits_sha256"])

    def sealed_document(self) -> dict[str, object]:
        return attach_digest(self.to_document())

    def bytes(self) -> bytes:
        return canonical_bytes(self.sealed_document())


@dataclass(frozen=True, slots=True)
class StageExitBundle:
    """Immutable seal recording acceptance or rejection and the receipts that back it.

    Exit acceptance is separate from process completion: an accepted exit must
    carry both reconciliation and inclusion-decision receipts and the id of the
    independent authorization that admitted the stage.
    """

    stage_id: StageId
    stage_kind: StageKind
    protocol_id: ProtocolId
    entry_bundle_sha256: str
    preceding_exit_sha256: str
    status: StageState
    reconciliation_sha256: str
    inclusion_decision_sha256: str
    authorization_id: StageAuthorizationId

    def __post_init__(self) -> None:
        if self.status not in {StageState.ACCEPTED, StageState.REJECTED}:
            raise StageControlError("stage_exit_status_invalid", (self.status.value,))
        _require_sha256(self.entry_bundle_sha256, "stage_exit_hash_invalid", "entry_bundle_sha256")
        _require_sha256(
            self.preceding_exit_sha256, "stage_exit_hash_invalid", "preceding_exit_sha256"
        )
        _require_sha256(
            self.reconciliation_sha256, "stage_exit_hash_invalid", "reconciliation_sha256"
        )
        _require_sha256(
            self.inclusion_decision_sha256, "stage_exit_hash_invalid", "inclusion_decision_sha256"
        )

    @property
    def is_accepted_and_complete(self) -> bool:
        return self.status is StageState.ACCEPTED

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": STAGE_BUNDLE_SCHEMA_VERSION,
            "artifact_type": "stage_exit_bundle",
            "stage_id": str(self.stage_id),
            "stage_kind": self.stage_kind.value,
            "protocol_id": str(self.protocol_id),
            "entry_bundle_sha256": self.entry_bundle_sha256,
            "preceding_exit_sha256": self.preceding_exit_sha256,
            "status": self.status.value,
            "reconciliation_sha256": self.reconciliation_sha256,
            "inclusion_decision_sha256": self.inclusion_decision_sha256,
            "authorization_id": str(self.authorization_id),
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def sealed_document(self) -> dict[str, object]:
        return attach_digest(self.to_document())

    def bytes(self) -> bytes:
        return canonical_bytes(self.sealed_document())


@dataclass(frozen=True, slots=True)
class StageAuthorization:
    """An out-of-band operator or scheduler grant to admit exactly one stage.

    It references the pre-execution entry digest and envelope only, so it can be
    minted before the stage runs and can never be produced from the stage's own
    exit or process completion.
    """

    authorization_id: StageAuthorizationId
    stage_id: StageId
    stage_kind: StageKind
    protocol_id: ProtocolId
    entry_bundle_sha256: str
    envelope_sha256: str
    authorizer_id: str
    authorizer_role: str
    valid_from: datetime
    valid_until: datetime
    paid_execution: bool

    def __post_init__(self) -> None:
        require_independent_authorizer_role(self.authorizer_role)
        if not isinstance(self.authorizer_id, str) or not self.authorizer_id:
            raise StageAuthorizationError("authorization_authorizer_invalid")
        _require_sha256(
            self.entry_bundle_sha256, "authorization_hash_invalid", "entry_bundle_sha256"
        )
        _require_sha256(self.envelope_sha256, "authorization_hash_invalid", "envelope_sha256")
        if self.valid_until <= self.valid_from:
            raise StageAuthorizationError("authorization_validity_invalid")

    def is_current(self, now: datetime) -> bool:
        return self.valid_from <= now < self.valid_until

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": STAGE_BUNDLE_SCHEMA_VERSION,
            "artifact_type": "stage_authorization",
            "authorization_id": str(self.authorization_id),
            "stage_id": str(self.stage_id),
            "stage_kind": self.stage_kind.value,
            "protocol_id": str(self.protocol_id),
            "entry_bundle_sha256": self.entry_bundle_sha256,
            "envelope_sha256": self.envelope_sha256,
            "authorizer_id": self.authorizer_id,
            "authorizer_role": self.authorizer_role,
            "valid_from": _stage_utc(self.valid_from, "valid_from"),
            "valid_until": _stage_utc(self.valid_until, "valid_until"),
            "paid_execution": self.paid_execution,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def sealed_document(self) -> dict[str, object]:
        return attach_digest(self.to_document())

    def bytes(self) -> bytes:
        return canonical_bytes(self.sealed_document())


def load_entry_bundle(data: bytes) -> StageEntryBundle:
    """Parse and integrity-check a sealed entry bundle, failing closed on tamper."""

    document = _canonical_stage_document(data, "stage_entry_bundle_corrupt")
    if document.get("artifact_type") != "stage_entry_bundle":
        raise StageControlError("stage_entry_bundle_corrupt")
    if not verify_digest(document):
        raise StageControlError("stage_entry_bundle_corrupt")
    try:
        bundle = StageEntryBundle(
            stage_id=StageId(document["stage_id"]),
            stage_kind=StageKind(document["stage_kind"]),
            protocol_id=ProtocolId(document["protocol_id"]),
            predecessor_stage_kind=StageKind(document["predecessor_stage_kind"]),
            locks={str(k): str(v) for k, v in dict(document["locks"]).items()},
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StageControlError("stage_entry_bundle_corrupt") from error
    if bundle.bytes() != data:
        raise StageControlError("stage_entry_bundle_corrupt")
    return bundle


def load_exit_bundle(data: bytes) -> StageExitBundle:
    """Parse and integrity-check a sealed predecessor exit bundle."""

    document = _canonical_stage_document(data, "predecessor_exit_corrupt")
    if document.get("artifact_type") != "stage_exit_bundle":
        raise StageControlError("predecessor_exit_corrupt")
    if not verify_digest(document):
        raise StageControlError("predecessor_exit_corrupt")
    try:
        bundle = StageExitBundle(
            stage_id=StageId(document["stage_id"]),
            stage_kind=StageKind(document["stage_kind"]),
            protocol_id=ProtocolId(document["protocol_id"]),
            entry_bundle_sha256=str(document["entry_bundle_sha256"]),
            preceding_exit_sha256=str(document["preceding_exit_sha256"]),
            status=StageState(document["status"]),
            reconciliation_sha256=str(document["reconciliation_sha256"]),
            inclusion_decision_sha256=str(document["inclusion_decision_sha256"]),
            authorization_id=StageAuthorizationId(document["authorization_id"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StageControlError("predecessor_exit_corrupt") from error
    if bundle.bytes() != data:
        raise StageControlError("predecessor_exit_corrupt")
    return bundle


def load_authorization(data: bytes) -> StageAuthorization:
    """Parse and integrity-check a sealed stage authorization."""

    document = _canonical_stage_document(data, "authorization_corrupt")
    if document.get("artifact_type") != "stage_authorization":
        raise StageAuthorizationError("authorization_corrupt")
    if not verify_digest(document):
        raise StageAuthorizationError("authorization_corrupt")
    try:
        authorization = StageAuthorization(
            authorization_id=StageAuthorizationId(document["authorization_id"]),
            stage_id=StageId(document["stage_id"]),
            stage_kind=StageKind(document["stage_kind"]),
            protocol_id=ProtocolId(document["protocol_id"]),
            entry_bundle_sha256=str(document["entry_bundle_sha256"]),
            envelope_sha256=str(document["envelope_sha256"]),
            authorizer_id=str(document["authorizer_id"]),
            authorizer_role=str(document["authorizer_role"]),
            valid_from=_parse_stage_utc(document["valid_from"]),
            valid_until=_parse_stage_utc(document["valid_until"]),
            paid_execution=_require_bool(document["paid_execution"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StageAuthorizationError("authorization_corrupt") from error
    if authorization.bytes() != data:
        raise StageAuthorizationError("authorization_corrupt")
    return authorization


def authorize_stage_entry(
    *,
    stage_kind: StageKind,
    entry_bundle: StageEntryBundle,
    predecessor_exit: StageExitBundle | None,
    authorization: StageAuthorization,
    now: datetime,
    cross_repository_qualified: bool = False,
) -> None:
    """Refuse stage entry with a typed status before any enrollment.

    No automatic fallback topology, self-promotion, or self-authorization is
    permitted. The predecessor exit authority must be present, intact, accepted,
    and complete, and the authorization must be independent and current.
    """

    if entry_bundle.stage_kind is not stage_kind:
        raise StageControlError("stage_kind_mismatch", (entry_bundle.stage_kind.value,))
    if predecessor_exit is None:
        raise StageControlError("missing_predecessor_exit", (stage_kind.value,))
    require_stage_predecessor(stage_kind, predecessor_exit.stage_kind)
    if predecessor_exit.status is StageState.REJECTED:
        raise StageControlError("predecessor_exit_rejected", (stage_kind.value,))
    if not predecessor_exit.is_accepted_and_complete:
        raise StageControlError("predecessor_exit_incomplete", (stage_kind.value,))
    if entry_bundle.locks["preceding_exit_sha256"] != predecessor_exit.digest:
        raise StageControlError("predecessor_exit_link_mismatch", (stage_kind.value,))
    if stage_kind is StageKind.CROSS_REPOSITORY and not cross_repository_qualified:
        raise StageControlError("cross_repository_qualification_required")
    if (
        authorization.stage_id != entry_bundle.stage_id
        or authorization.protocol_id != entry_bundle.protocol_id
        or authorization.stage_kind is not stage_kind
    ):
        raise StageAuthorizationError("authorization_scope_mismatch")
    if authorization.entry_bundle_sha256 != entry_bundle.digest:
        raise StageAuthorizationError("authorization_scope_mismatch")
    if authorization.envelope_sha256 != entry_bundle.envelope_sha256:
        raise StageAuthorizationError("authorization_envelope_mismatch")
    require_independent_authorizer_role(authorization.authorizer_role)
    if not authorization.is_current(now):
        raise StageAuthorizationError("stale_authorization")


@dataclass(frozen=True, slots=True)
class StageUnit:
    """One planned or terminal unit of stage work, for resume planning only."""

    unit_id: str
    terminal: bool


def plan_stage_resume(
    units: Sequence[StageUnit],
    *,
    authorization: StageAuthorization,
    now: datetime,
    locks_verified: bool,
    receipts_consistent: bool,
    ledger_cas_consistent: bool,
    circuit_breaker_open: bool,
) -> tuple[str, ...]:
    """Return only the unfinished planned unit ids that may be resumed.

    Terminal units are never rerun and no attempt is replaced. Any unverified
    precondition fails closed so partial evidence is preserved untouched.
    """

    if circuit_breaker_open:
        raise StageControlError("resume_circuit_breaker_open")
    if not authorization.is_current(now):
        raise StageAuthorizationError("stale_authorization")
    if not locks_verified:
        raise StageControlError("resume_lock_drift")
    if not receipts_consistent:
        raise StageControlError("resume_receipt_conflict")
    if not ledger_cas_consistent:
        raise StageControlError("resume_ledger_cas_conflict")
    return tuple(unit.unit_id for unit in units if not unit.terminal)


class StageBundleStore:
    """Content-addressed, write-once persistence for stage bundles and authorizations."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root) / "stage-bundles"

    def seal_entry(self, bundle: StageEntryBundle) -> tuple[Path, str]:
        """Persist an entry seal idempotently; a conflicting reseal fails closed."""

        path = self._root / f"entry-{bundle.stage_id}.{bundle.protocol_id}.json"
        outcome = self._write_once(path, bundle.bytes(), "stage_bundle_mutation")
        return path, outcome

    def seal_exit(self, bundle: StageExitBundle) -> tuple[Path, str]:
        path = self._root / f"exit-{bundle.stage_id}.{bundle.entry_bundle_sha256}.json"
        outcome = self._write_once(path, bundle.bytes(), "stage_exit_mutation")
        return path, outcome

    def record_authorization(self, authorization: StageAuthorization) -> tuple[Path, str]:
        path = self._root / f"authorization-{authorization.authorization_id}.json"
        outcome = self._write_once(path, authorization.bytes(), "authorization_mutation")
        return path, outcome

    def _write_once(self, path: Path, data: bytes, conflict_code: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if not path.is_file() or path.read_bytes() != data:
                raise StageControlError(conflict_code, (path.name,))
            return "reused"
        staged = path.with_name(f".{path.name}.{os.getpid()}.staged")
        try:
            with staged.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # Publish atomically and exclusively on the final path itself: the
                # fully written staged file is hard-linked into place, so a losing
                # concurrent writer sees FileExistsError over complete bytes and can
                # never silently clobber a differing seal.
                os.link(staged, path)
            except FileExistsError:
                if not path.is_file() or path.read_bytes() != data:
                    raise StageControlError(conflict_code, (path.name,)) from None
                return "reused"
        finally:
            if staged.exists():
                staged.unlink()
        return "sealed"


def stage_status_projection(
    *,
    stage_id: StageId,
    stage_kind: StageKind,
    state: StageState,
    planned: int,
    started: int,
    terminal: int,
    reconciliation_complete: bool,
    headroom: Mapping[str, object],
    throttle_healthy: bool,
    model_healthy: bool,
    evidence_loss_signals: int,
    authorization_valid_until: datetime,
    now: datetime,
) -> dict[str, object]:
    """Project a treatment-neutral, outcome-blind local status document.

    It reports counts and health only; it never records an outcome, a treatment
    label, or any repository or credential material.
    """

    if planned < 0 or started < 0 or terminal < 0 or evidence_loss_signals < 0:
        raise StageControlError("stage_status_counts_invalid")
    authorization_expired = now >= authorization_valid_until
    exhausted = bool(headroom.get("exhausted"))
    return {
        "schema_version": STAGE_BUNDLE_SCHEMA_VERSION,
        "artifact_type": "stage_status",
        "stage_id": str(stage_id),
        "stage_kind": stage_kind.value,
        "state": state.value,
        "counts": {"planned": planned, "started": started, "terminal": terminal},
        "reconciliation_complete": bool(reconciliation_complete),
        "headroom": dict(headroom),
        "throttle_healthy": bool(throttle_healthy),
        "model_healthy": bool(model_healthy),
        "evidence_loss_signals": evidence_loss_signals,
        "authorization_expiry": _stage_utc(authorization_valid_until, "authorization_valid_until"),
        "authorization_expired": authorization_expired,
        "pause_new_work": bool(
            exhausted
            or authorization_expired
            or evidence_loss_signals > 0
            or not throttle_healthy
            or not model_healthy
        ),
    }


_STAGE_ALERTS: tuple[tuple[str, str], ...] = (
    ("lock_drift", "pause new work and require a new protocol or stage identity"),
    ("incomplete_evidence", "pause new work and reconcile before any acceptance"),
    ("categorical_blocker", "stop the affected family; never relax a threshold"),
    ("exhausted_envelope", "stop new attempts; a new sealed envelope is required"),
    ("stale_authorization", "pause new work until an operator or scheduler re-authorizes"),
    ("backup_failure", "pause paid work until backup and restore proof passes"),
    ("dg_r_revocation", "disable the entire cross-repository stage"),
)


def stage_alert_actions() -> dict[str, str]:
    """Return the frozen alert-to-action map; alerts pause work, never mutate evidence."""

    return dict(_STAGE_ALERTS)


def _require_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise StageControlError("stage_bundle_field_invalid", ("paid_execution",))
    return value


def _parse_stage_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise StageControlError("stage_timestamp_not_utc")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StageControlError("stage_timestamp_not_utc")
    return parsed.astimezone(UTC)


def _canonical_stage_document(data: bytes, code: str) -> dict[str, object]:
    import json

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise StageControlError(code)
            result[key] = value
        return result

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, ValueError) as error:
        raise StageControlError(code) from error
    if not isinstance(document, dict):
        raise StageControlError(code)
    try:
        canonical = canonical_bytes(document)
    except CanonicalizationError as error:
        raise StageControlError(code) from error
    if canonical != data:
        raise StageControlError(code)
    return document
