"""Frozen, scope-bound language for immutable claim decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import AnalysisError

from .gates import ClaimGateDecision

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
SOURCE_KINDS = frozenset(
    {
        "completed_reconciled_product",
        "construction",
        "component_test",
        "deterministic_fixture",
        "unreconciled_trial",
        "engine_upper_bound",
        "pilot",
    }
)
RELEASE_STATEMENT_KINDS = frozenset(
    {
        "bounded_product_regression",
        "observation_qualification",
        "confirmatory_shipped_product_efficacy",
        "randomized_treatment_estimand",
    }
)
_PROHIBITED_LANGUAGE = (
    "safe",
    "zero risk",
    "no risk",
    "shipped-product confirmatory efficacy",
    "product efficacy",
)


def _require_sha256(value: str, code: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise AnalysisError(code)


@dataclass(frozen=True, slots=True)
class ClaimScope:
    """The tested scope that must accompany every rendered claim."""

    protocol_sha256: str
    population_id: str
    model_id: str
    endpoint_id: str
    stratum: str
    history_regime: str
    environment_sha256: str
    source_sha256: tuple[str, ...]
    derivation_sha256: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.protocol_sha256, self.environment_sha256, self.derivation_sha256):
            _require_sha256(value, "claim_scope_lineage_invalid")
        if not all(
            isinstance(value, str) and value
            for value in (
                self.population_id,
                self.model_id,
                self.endpoint_id,
                self.stratum,
                self.history_regime,
            )
        ):
            raise AnalysisError("claim_scope_dimension_missing")
        if not self.source_sha256 or not all(
            _SHA256.fullmatch(value) is not None for value in self.source_sha256
        ):
            raise AnalysisError("claim_scope_source_missing")
        if not self.evidence_ids or any(not value for value in self.evidence_ids):
            raise AnalysisError("claim_scope_evidence_missing")

    @property
    def scope_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "protocol_sha256": self.protocol_sha256,
            "population_id": self.population_id,
            "model_id": self.model_id,
            "endpoint_id": self.endpoint_id,
            "stratum": self.stratum,
            "history_regime": self.history_regime,
            "environment_sha256": self.environment_sha256,
            "source_sha256": sorted(self.source_sha256),
            "derivation_sha256": self.derivation_sha256,
            "evidence_ids": sorted(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class ReleaseClaimScope:
    """Exact, privacy-preserving scope used to bind release evidence to one statement.

    This is deliberately stricter than ``ClaimScope``.  Story 5.7 claim scope
    remains the authority for statistical decisions; this extension prevents
    release evidence from being reused across configured product paths.
    """

    artifact_id: str
    artifact_sha256: str
    evidence_id: str
    path: str
    observation_mode: str
    configuration_sha256: str
    source_implementation_sha256: str
    runtime_lock_sha256: str
    version_sha256: str
    protocol_sha256: str
    population_id: str
    model_id: str
    stratum: str
    history_regime: str
    endpoint_id: str
    estimand_id: str
    source_sha256: tuple[str, ...]
    derivation_sha256: str
    reconciliation_sha256: str
    gate_id: str
    gate_sha256: str

    def __post_init__(self) -> None:
        hashes = (
            self.artifact_sha256,
            self.configuration_sha256,
            self.source_implementation_sha256,
            self.runtime_lock_sha256,
            self.version_sha256,
            self.protocol_sha256,
            self.derivation_sha256,
            self.reconciliation_sha256,
            self.gate_sha256,
        )
        if not all(_SHA256.fullmatch(value) for value in hashes):
            raise AnalysisError("release_claim_scope_lineage_invalid")
        source = tuple(sorted(set(self.source_sha256)))
        if not source or not all(_SHA256.fullmatch(value) for value in source):
            raise AnalysisError("release_claim_scope_source_missing")
        if self.stratum not in {"product", "engine"}:
            raise AnalysisError("release_claim_scope_stratum_invalid")
        if not all(
            isinstance(value, str) and value
            for value in (
                self.artifact_id,
                self.evidence_id,
                self.path,
                self.observation_mode,
                self.population_id,
                self.model_id,
                self.history_regime,
                self.endpoint_id,
                self.estimand_id,
                self.gate_id,
            )
        ):
            raise AnalysisError("release_claim_scope_dimension_missing")
        object.__setattr__(self, "source_sha256", source)

    @property
    def scope_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
            "evidence_id": self.evidence_id,
            "path": self.path,
            "observation_mode": self.observation_mode,
            "configuration_sha256": self.configuration_sha256,
            "source_implementation_sha256": self.source_implementation_sha256,
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "version_sha256": self.version_sha256,
            "protocol_sha256": self.protocol_sha256,
            "population_id": self.population_id,
            "model_id": self.model_id,
            "stratum": self.stratum,
            "history_regime": self.history_regime,
            "endpoint_id": self.endpoint_id,
            "estimand_id": self.estimand_id,
            "source_sha256": list(self.source_sha256),
            "derivation_sha256": self.derivation_sha256,
            "reconciliation_sha256": self.reconciliation_sha256,
            "gate_id": self.gate_id,
            "gate_sha256": self.gate_sha256,
        }


@dataclass(frozen=True, slots=True)
class BoundedClaim:
    """A terminal statement generated only from the Story 5.4 authority."""

    claim_id: str
    decision_sha256: str
    terminal_status: str
    source_kind: str
    reproduction_status: str
    scope: ClaimScope
    language: str

    def __post_init__(self) -> None:
        if self.terminal_status not in {"positive", "null", "harmful", "indeterminate"}:
            raise AnalysisError("bounded_claim_status_invalid")
        if self.source_kind not in SOURCE_KINDS:
            raise AnalysisError("bounded_claim_source_kind_invalid")
        if self.reproduction_status not in {"verified", "pending", "failed"}:
            raise AnalysisError("bounded_claim_reproduction_invalid")
        _require_sha256(self.decision_sha256, "bounded_claim_decision_invalid")
        lint_claim_text(self.language)

    @property
    def claim_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "bounded_claim",
            "claim_id": self.claim_id,
            "decision_sha256": self.decision_sha256,
            "terminal_status": self.terminal_status,
            "source_kind": self.source_kind,
            "reproduction_status": self.reproduction_status,
            "scope": self.scope.to_document(),
            "language": self.language,
        }


def lint_claim_text(text: str) -> None:
    """Reject language that changes a bounded evidence statement into promotion."""
    if not isinstance(text, str) or not text:
        raise AnalysisError("bounded_claim_language_missing")
    lowered = text.casefold()
    if any(term in lowered for term in _PROHIBITED_LANGUAGE):
        raise AnalysisError("bounded_claim_language_forbidden")


def bound_claim(
    decision: ClaimGateDecision,
    scope: ClaimScope,
    *,
    source_kind: str,
    reproduction_status: str,
) -> BoundedClaim:
    """Project a typed claim decision without recalculating a statistic or threshold."""
    if decision.endpoint_id != scope.endpoint_id:
        raise AnalysisError("bounded_claim_endpoint_scope_conflict")
    if decision.protocol_sha256 != scope.protocol_sha256:
        raise AnalysisError("bounded_claim_protocol_scope_conflict")
    if source_kind not in SOURCE_KINDS:
        raise AnalysisError("bounded_claim_source_kind_invalid")
    if reproduction_status not in {"verified", "pending", "failed"}:
        raise AnalysisError("bounded_claim_reproduction_invalid")

    promotable = source_kind == "completed_reconciled_product" and reproduction_status == "verified"
    if not promotable:
        status = "indeterminate"
        language = _nonconfirmatory_language(source_kind, reproduction_status)
    elif decision.status == "pass":
        status = "positive"
        language = (
            f"{decision.claim_type} passed its frozen decision gates for the tested scope; "
            "this statement is limited to that protocol, population, model, stratum, "
            "and history regime."
        )
    elif decision.status == "fail" and decision.claim_type == "no_regression":
        status = "harmful"
        language = (
            "The frozen non-inferiority decision did not pass for the tested scope; "
            "the result does not support a no-harm conclusion."
        )
    elif decision.status == "fail":
        status = "null"
        language = (
            f"{decision.claim_type} did not pass its frozen decision gates for the tested scope."
        )
    else:
        status = "indeterminate"
        language = (
            f"{decision.claim_type} is {decision.status} under its immutable decision record; "
            "no efficacy conclusion is supported."
        )
    return BoundedClaim(
        claim_id=decision.claim_id,
        decision_sha256=decision.decision_sha256,
        terminal_status=status,
        source_kind=source_kind,
        reproduction_status=reproduction_status,
        scope=scope,
        language=language,
    )


def _nonconfirmatory_language(source_kind: str, reproduction_status: str) -> str:
    labels = {
        "construction": "Evaluator construction",
        "component_test": "Component-test evidence",
        "deterministic_fixture": "Deterministic-fixture evidence",
        "unreconciled_trial": "Unreconciled trial evidence",
        "engine_upper_bound": "Direct-engine evidence is an engine upper bound",
        "pilot": "Pilot evidence",
        "completed_reconciled_product": "Completed product evidence",
    }
    verification = (
        "reproduction evidence is pending"
        if reproduction_status == "pending"
        else "reproduction evidence failed"
        if reproduction_status == "failed"
        else "the source is not confirmatory product evidence"
    )
    return f"{labels[source_kind]}; {verification}; it cannot support a release-fitness conclusion."
