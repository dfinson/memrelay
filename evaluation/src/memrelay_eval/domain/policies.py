"""Domain lifecycle policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .errors import (
    IneligibleEnrollmentError,
    InvalidLifecycleTransitionError,
    SecretConfigurationError,
)
from .states import AttemptTerminalKind, EvaluationStratum, RunState

if TYPE_CHECKING:
    from .entities import (
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
