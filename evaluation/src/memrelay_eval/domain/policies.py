"""Domain lifecycle policy."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .errors import (
    ControlledHistoryViolationError,
    IneligibleEnrollmentError,
    InvalidLifecycleTransitionError,
    SecretConfigurationError,
    StageAuthorizationError,
    StageControlError,
)
from .states import (
    AttemptTerminalKind,
    EvaluationStratum,
    ProbeWriteDisposition,
    RunState,
    StageKind,
    StageState,
)

if TYPE_CHECKING:
    from .entities import (
        ArtifactRef,
        AttemptTerminal,
        ExposureDecision,
        FreshIsolationAttestation,
        ProductIdentityChain,
        Protocol,
    )

_ALLOWED_TRANSITIONS = {
    RunState.PLANNED: frozenset({RunState.ASSIGNED}),
    RunState.ASSIGNED: frozenset({RunState.PROVISIONED}),
    RunState.PROVISIONED: frozenset({RunState.RUNNING}),
    RunState.RUNNING: frozenset({RunState.EXPORTED}),
    RunState.EXPORTED: frozenset({RunState.SCORED}),
    RunState.SCORED: frozenset({RunState.RECONCILED}),
    RunState.RECONCILED: frozenset({RunState.INCLUDED, RunState.EXCLUDED}),
    RunState.INCLUDED: frozenset(),
    RunState.EXCLUDED: frozenset(),
}


def validate_run_transition(previous: RunState, next_state: RunState) -> None:
    """Raise unless the requested edge is part of the frozen run graph."""

    if next_state not in _ALLOWED_TRANSITIONS[previous]:
        raise InvalidLifecycleTransitionError(
            f"invalid run transition: {previous.value} -> {next_state.value}"
        )


_SHA256 = re.compile(r"^[a-f0-9]{64}$")

# The frozen study-stage lifecycle. Independent authorization is the only edge
# that leaves ``planned``; process completion never advances a stage. Terminal
# states have no outgoing edge, so a rejected stage can never be re-entered.
_ALLOWED_STAGE_TRANSITIONS: dict[StageState, frozenset[StageState]] = {
    StageState.PLANNED: frozenset({StageState.AUTHORIZED, StageState.REJECTED}),
    StageState.AUTHORIZED: frozenset({StageState.RUNNING, StageState.REJECTED}),
    StageState.RUNNING: frozenset({StageState.PAUSED, StageState.CLOSING, StageState.REJECTED}),
    StageState.PAUSED: frozenset({StageState.RUNNING, StageState.CLOSING, StageState.REJECTED}),
    StageState.CLOSING: frozenset({StageState.ACCEPTED, StageState.REJECTED}),
    StageState.ACCEPTED: frozenset(),
    StageState.REJECTED: frozenset(),
}

# The fixed forward progression. Each enrollable stage requires exactly one
# immutable predecessor exit; there is no skip, self-promotion, or alternate
# topology. Cross-repository additionally requires a current DG-R qualification,
# enforced separately at the orchestration entry guard.
_REQUIRED_PREDECESSOR: dict[StageKind, StageKind] = {
    StageKind.INTEGRATION: StageKind.CONFORMANCE,
    StageKind.PILOT: StageKind.INTEGRATION,
    StageKind.PRIMARY: StageKind.PILOT,
    StageKind.SECONDARY: StageKind.PRIMARY,
    StageKind.CROSS_REPOSITORY: StageKind.PRIMARY,
}

# Exactly the twelve frozen inputs bound by a sealed stage entry bundle.
STAGE_ENTRY_LOCK_FIELDS: frozenset[str] = frozenset(
    {
        "catalog_sha256",
        "protocol_sha256",
        "sdk_sha256",
        "runtime_lock_sha256",
        "model_lock_sha256",
        "environment_sha256",
        "grader_sha256",
        "judge_sha256",
        "telemetry_sha256",
        "price_table_sha256",
        "limits_sha256",
        "preceding_exit_sha256",
    }
)

_INDEPENDENT_AUTHORIZER_ROLES: frozenset[str] = frozenset({"operator", "scheduler"})

# The enrollable stages the non-interactive ``run`` command drives through the
# entry guard. Cross-repository is recognized but is denied before discovery by
# the Story 7.3 deny-by-default authorization, so it is not enrolled here.
ENROLLABLE_STAGES: frozenset[StageKind] = frozenset(
    {
        StageKind.INTEGRATION,
        StageKind.PILOT,
        StageKind.PRIMARY,
        StageKind.SECONDARY,
    }
)


def validate_stage_transition(previous: StageState, next_state: StageState) -> None:
    """Raise a typed refusal unless the stage edge is part of the frozen graph."""

    if next_state not in _ALLOWED_STAGE_TRANSITIONS[previous]:
        raise StageControlError("invalid_stage_transition", (previous.value, next_state.value))


def required_predecessor_stage(stage: StageKind) -> StageKind:
    """Return the sole immutable predecessor an entry stage must accept first."""

    predecessor = _REQUIRED_PREDECESSOR.get(stage)
    if predecessor is None:
        raise StageControlError("stage_has_no_enrollable_predecessor", (stage.value,))
    return predecessor


def require_stage_predecessor(stage: StageKind, predecessor: StageKind) -> StageKind:
    """Reject a skipped or mis-ordered predecessor with no topology fallback."""

    expected = required_predecessor_stage(stage)
    if predecessor is not expected:
        raise StageControlError("stage_skipped", (stage.value, predecessor.value))
    return expected


def require_stage_entry_locks(locks: Mapping[str, object]) -> None:
    """Require exactly the twelve frozen entry hashes as lowercase SHA-256 values."""

    if set(locks) != STAGE_ENTRY_LOCK_FIELDS:
        missing = sorted(STAGE_ENTRY_LOCK_FIELDS - set(locks))
        extra = sorted(set(locks) - STAGE_ENTRY_LOCK_FIELDS)
        raise StageControlError("stage_bundle_incomplete", (*missing, *extra))
    for field, value in locks.items():
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise StageControlError("stage_bundle_hash_invalid", (field,))


def require_independent_authorizer_role(role: object) -> str:
    """Reject any authorizer that is not an independent operator or scheduler.

    Successful construction, reconciliation, or process completion is never an
    authorizer; only these two out-of-band roles may authorize the next stage.
    """

    if not isinstance(role, str) or role not in _INDEPENDENT_AUTHORIZER_ROLES:
        raise StageAuthorizationError("self_authorization_denied", (str(role),))
    return role


def retry_eligibility_denial_code(
    protocol: Protocol,
    terminal: AttemptTerminal,
    exposure: ExposureDecision | None,
    isolation: FreshIsolationAttestation | None,
) -> str | None:
    """Return the sole domain-owned denial code for a retry eligibility request."""

    if not protocol.allows_pre_exposure_infrastructure_retry:
        return "retry_not_authorized_by_protocol"
    if terminal.classification is not AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE:
        return "retry_terminal_not_pre_exposure_infrastructure_failure"
    if exposure is None or not exposure.is_conclusively_unexposed:
        return "retry_exposure_not_conclusively_unexposed"
    if isolation is None or not isolation.is_conclusive:
        return "retry_fresh_isolation_unattested"
    return None


_TREATMENT_TERMS = frozenset({"arm", "treatment", "control", "variant"})
_SECRET_TERMS = frozenset({"api_key", "apikey", "credential", "password", "secret"})
_SECRET_PREFIXES = ("sk-", "ghp_", "github_pat_", "eyj")


def require_treatment_neutral(value: object) -> None:
    """Reject treatment-revealing plan content before it receives an identity."""
    _walk_treatment_neutral(value)


def require_no_secret_values(value: object) -> None:
    """Reject ordinary secret-bearing values without retaining their contents."""
    _walk_no_secret_values(value)


def require_eligible_disposition(disposition: Mapping[str, object]) -> None:
    """Honor a precomputed Story 1.4 disposition without reimplementing its policy."""
    if disposition.get("disposition") != "eligible":
        raise IneligibleEnrollmentError(IneligibleEnrollmentError.code)


def require_same_evaluation_stratum(strata: Sequence[EvaluationStratum]) -> EvaluationStratum:
    """Reject pooled analysis across distinct treatment strata."""
    if not strata:
        raise ValueError("evaluation stratum is required")
    first = strata[0]
    if any(stratum is not first for stratum in strata[1:]):
        raise ValueError("evaluation stratum pooling is forbidden")
    return first


def require_same_product_identity_chain(
    chains: Sequence[ProductIdentityChain],
) -> ProductIdentityChain:
    """Reject pooled product or engine identity chains without a stratified operation."""
    if not chains:
        raise ValueError("product identity chain is required")
    first = chains[0]
    if any(chain != first for chain in chains[1:]):
        raise ValueError("product identity pooling is forbidden")
    return first


def require_single_product_stratum(
    chains: Sequence[ProductIdentityChain],
) -> EvaluationStratum:
    """Guard the production aggregation boundary against product/engine pooling."""

    return require_same_evaluation_stratum(tuple(chain.stratum for chain in chains))


def enforce_probe_write_disposition(
    disposition: ProbeWriteDisposition,
    *,
    write_attempted: bool,
    write_persisted: bool,
    recorded_evidence: ArtifactRef | None,
) -> None:
    """Enforce the frozen controlled-history probe-write handling; nothing is inferred.

    ``disabled`` forbids any attempted write. ``discarded`` permits an attempt but the
    write must never observably persist. ``recorded_separately`` permits an attempt only
    when it is backed by its own immutable evidence, never folded into the source bundle.
    """

    if not write_attempted:
        return
    if disposition is ProbeWriteDisposition.DISABLED:
        raise ControlledHistoryViolationError("controlled_probe_write_disabled")
    if disposition is ProbeWriteDisposition.DISCARDED and write_persisted:
        raise ControlledHistoryViolationError("controlled_probe_write_not_discarded")
    if disposition is ProbeWriteDisposition.RECORDED_SEPARATELY and recorded_evidence is None:
        raise ControlledHistoryViolationError("controlled_probe_write_not_separately_recorded")


def _walk_treatment_neutral(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or any(term in key.casefold() for term in _TREATMENT_TERMS):
                from .errors import DomainError

                raise DomainError("treatment-revealing enrollment content is forbidden")
            _walk_treatment_neutral(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _walk_treatment_neutral(nested)
    elif isinstance(value, str):
        normalized = value.casefold()
        if normalized in _TREATMENT_TERMS or any(
            term in normalized for term in ("treatment", "arm=")
        ):
            from .errors import DomainError

            raise DomainError("treatment-revealing enrollment content is forbidden")


def _walk_no_secret_values(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or any(term in key.casefold() for term in _SECRET_TERMS):
                raise SecretConfigurationError()
            _walk_no_secret_values(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _walk_no_secret_values(nested)
    elif isinstance(value, (bytes, bytearray)):
        raise SecretConfigurationError()
    elif isinstance(value, str):
        normalized = value.casefold()
        if normalized.startswith(_SECRET_PREFIXES) or any(
            term in normalized for term in _SECRET_TERMS
        ):
            raise SecretConfigurationError()
