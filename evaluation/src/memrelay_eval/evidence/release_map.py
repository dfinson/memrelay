"""Immutable, local-only mappings from qualified evidence to bounded statements."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from memrelay_eval.analysis.claims import RELEASE_STATEMENT_KINDS, ReleaseClaimScope
from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import AnalysisError

_EVIDENCE_CLASSES = frozenset(
    {
        "fixture_retrieval",
        "fixture_roundtrip",
        "observation_sentinel",
        "observational_characterization",
        "randomized_reconciled_product",
        "engine_upper_bound",
        "pilot",
    }
)
_EVIDENCE_TERMINAL_STATUSES = frozenset(
    {
        "passed",
        "null",
        "harmful",
        "indeterminate",
        "expired",
        "drifted",
        "conflicting",
        "missing",
        "blocked",
    }
)
_GATE_STATUSES = frozenset({"passed", "failed", "expired", "drifted", "conflicting", "missing"})
_DECISION_STATUSES = frozenset(
    {"positive", "null", "harmful", "indeterminate", "estimation-only", "unqualified", "blocked"}
)
_FIXTURE_CLAIMS = {
    "EV-FIXTURE-RETRIEVAL": ("fixture_retrieval", "CL-WIRING-RANK"),
    "EV-ROUNDTRIP-MCP": ("fixture_roundtrip", "CL-PIPELINE-SEAM"),
}
_UNSUPPORTED_EXCLUSIONS = (
    "safety",
    "economics",
    "generalization",
    "production completeness",
    "cross-repository fitness",
)


def _require_nonempty_ascii(value: str, code: str) -> None:
    if not isinstance(value, str) or not value or not value.isascii():
        raise AnalysisError(code)


@dataclass(frozen=True, slots=True)
class ReleaseEvidence:
    """One immutable evidence artifact and the sole statement it can support."""

    scope: ReleaseClaimScope
    evidence_class: str
    supported_statement: str
    exclusions: tuple[str, ...]
    terminal_status: str
    gate_status: str

    def __post_init__(self) -> None:
        if self.evidence_class not in _EVIDENCE_CLASSES:
            raise AnalysisError("release_evidence_class_invalid")
        if self.terminal_status not in _EVIDENCE_TERMINAL_STATUSES:
            raise AnalysisError("release_evidence_terminal_status_invalid")
        if self.gate_status not in _GATE_STATUSES:
            raise AnalysisError("release_evidence_gate_status_invalid")
        _require_nonempty_ascii(self.supported_statement, "release_evidence_statement_missing")
        exclusions = tuple(sorted(set(self.exclusions)))
        if not exclusions or any(not item.isascii() or not item for item in exclusions):
            raise AnalysisError("release_evidence_exclusions_missing")
        fixture = _FIXTURE_CLAIMS.get(self.scope.evidence_id)
        if fixture is not None and (
            self.evidence_class != fixture[0] or self.supported_statement != fixture[1]
        ):
            raise AnalysisError("release_evidence_fixture_identity_conflict")
        if self.evidence_class == "fixture_retrieval" and (
            self.scope.evidence_id != "EV-FIXTURE-RETRIEVAL"
            or self.supported_statement != "CL-WIRING-RANK"
        ):
            raise AnalysisError("release_evidence_fixture_claim_conflict")
        if self.evidence_class == "fixture_roundtrip" and (
            self.scope.evidence_id != "EV-ROUNDTRIP-MCP"
            or self.supported_statement != "CL-PIPELINE-SEAM"
        ):
            raise AnalysisError("release_evidence_fixture_claim_conflict")
        object.__setattr__(self, "exclusions", exclusions)

    @property
    def evidence_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "release_evidence",
            "scope": self.scope.to_document(),
            "evidence_class": self.evidence_class,
            "supported_statement": self.supported_statement,
            "exclusions": list(self.exclusions),
            "terminal_status": self.terminal_status,
            "gate_status": self.gate_status,
        }


@dataclass(frozen=True, slots=True)
class ReleaseStatement:
    """A requested release statement that must resolve to one exact evidence record."""

    statement_id: str
    statement_kind: str
    scope: ReleaseClaimScope
    statement: str

    def __post_init__(self) -> None:
        _require_nonempty_ascii(self.statement_id, "release_statement_id_missing")
        if self.statement_kind not in RELEASE_STATEMENT_KINDS:
            raise AnalysisError("release_statement_kind_invalid")
        _require_nonempty_ascii(self.statement, "release_statement_text_missing")

    def to_document(self) -> dict[str, object]:
        return {
            "statement_id": self.statement_id,
            "statement_kind": self.statement_kind,
            "scope": self.scope.to_document(),
            "statement": self.statement,
        }


@dataclass(frozen=True, slots=True)
class ReleaseMapPolicy:
    """Frozen policy that makes the exceptional unqualified label explicit."""

    allow_unqualified: bool = False

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "release_map_policy",
            "allow_unqualified": self.allow_unqualified,
        }


@dataclass(frozen=True, slots=True)
class ReleaseEvidenceDecision:
    """Typed, privacy-preserving terminal result for one proposed statement."""

    statement_id: str
    evidence_id: str
    evidence_sha256: str
    terminal_status: str
    supports: str
    does_not_support: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.terminal_status not in _DECISION_STATUSES:
            raise AnalysisError("release_evidence_decision_status_invalid")
        for value in (self.statement_id, self.evidence_id, self.evidence_sha256):
            _require_nonempty_ascii(value, "release_evidence_decision_identity_missing")
        if len(self.evidence_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.evidence_sha256
        ):
            raise AnalysisError("release_evidence_decision_hash_invalid")
        if not self.supports.startswith("Supports only ") or not self.does_not_support.startswith(
            "Does not support "
        ):
            raise AnalysisError("release_evidence_decision_language_invalid")
        reasons = tuple(sorted(set(self.reasons)))
        if any(not reason.isascii() or not reason for reason in reasons):
            raise AnalysisError("release_evidence_decision_reason_invalid")
        object.__setattr__(self, "reasons", reasons)

    @property
    def decision_sha256(self) -> str:
        return canonical_digest(self._basis())

    def to_document(self) -> dict[str, object]:
        return {**self._basis(), "decision_sha256": self.decision_sha256}

    def _basis(self) -> dict[str, object]:
        return {
            "statement_id": self.statement_id,
            "evidence_id": self.evidence_id,
            "evidence_sha256": self.evidence_sha256,
            "terminal_status": self.terminal_status,
            "supports": self.supports,
            "does_not_support": self.does_not_support,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class ReleaseEvidenceMap:
    """Canonical collection of evidence-to-statement decisions for a local report."""

    policy: ReleaseMapPolicy
    evidence: tuple[ReleaseEvidence, ...]
    statements: tuple[ReleaseStatement, ...]
    decisions: tuple[ReleaseEvidenceDecision, ...]

    def __post_init__(self) -> None:
        evidence = tuple(self.evidence)
        statements = tuple(self.statements)
        decisions = tuple(self.decisions)
        if not statements or len(statements) != len(decisions):
            raise AnalysisError("release_evidence_map_incomplete")
        if len({item.scope.evidence_id for item in evidence}) != len(evidence):
            raise AnalysisError("release_evidence_map_evidence_duplicate")
        if len({item.statement_id for item in statements}) != len(statements):
            raise AnalysisError("release_evidence_map_statement_duplicate")
        if {item.statement_id for item in statements} != {item.statement_id for item in decisions}:
            raise AnalysisError("release_evidence_map_decision_incomplete")
        evidence_by_id = {item.scope.evidence_id: item for item in evidence}
        decision_by_statement = {item.statement_id: item for item in decisions}
        for statement in statements:
            item = evidence_by_id.get(statement.scope.evidence_id)
            expected = (
                _decision_without_evidence(statement, self.policy, "evidence_missing")
                if item is None
                else _evaluate(item, statement, self.policy)
            )
            actual = decision_by_statement[statement.statement_id]
            if actual.to_document() != expected.to_document():
                raise AnalysisError("release_evidence_map_decision_conflict")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "statements", statements)
        object.__setattr__(self, "decisions", decisions)

    @property
    def map_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "release_evidence_map",
            "policy": self.policy.to_document(),
            "evidence": [item.to_document() for item in self.evidence],
            "statements": [item.to_document() for item in self.statements],
            "decisions": [item.to_document() for item in self.decisions],
        }


def map_release_evidence(
    evidence: tuple[ReleaseEvidence, ...],
    statements: tuple[ReleaseStatement, ...],
    *,
    policy: ReleaseMapPolicy | None = None,
) -> ReleaseEvidenceMap:
    """Map exact immutable scopes without allowing favorable evidence substitution."""

    policy = policy or ReleaseMapPolicy()
    by_evidence_id = {item.scope.evidence_id: item for item in evidence}
    decisions: list[ReleaseEvidenceDecision] = []
    for statement in statements:
        item = by_evidence_id.get(statement.scope.evidence_id)
        if item is None:
            decisions.append(_decision_without_evidence(statement, policy, "evidence_missing"))
            continue
        decisions.append(_evaluate(item, statement, policy))
    return ReleaseEvidenceMap(policy, evidence, statements, tuple(decisions))


def release_evidence_map_from_document(value: Mapping[str, object]) -> ReleaseEvidenceMap:
    """Load only a complete canonical map and independently recompute its decisions."""

    if set(value) != {
        "schema_version",
        "artifact_type",
        "policy",
        "evidence",
        "statements",
        "decisions",
    } or (
        value.get("schema_version") != "1.0.0"
        or value.get("artifact_type") != "release_evidence_map"
    ):
        raise AnalysisError("release_evidence_map_schema_invalid")
    try:
        policy_value = _mapping(value["policy"], "policy")
        if set(policy_value) != {"schema_version", "artifact_type", "allow_unqualified"}:
            raise AnalysisError("release_evidence_map_policy_invalid")
        policy = ReleaseMapPolicy(
            allow_unqualified=_boolean(
                policy_value["allow_unqualified"], "policy.allow_unqualified"
            )
        )
        if (
            policy_value["schema_version"] != "1.0.0"
            or policy_value["artifact_type"] != "release_map_policy"
        ):
            raise AnalysisError("release_evidence_map_policy_invalid")
        evidence = tuple(
            _evidence_from_document(item) for item in _list(value["evidence"], "evidence")
        )
        statements = tuple(
            _statement_from_document(item) for item in _list(value["statements"], "statements")
        )
        decisions = tuple(
            _decision_from_document(item) for item in _list(value["decisions"], "decisions")
        )
        result = ReleaseEvidenceMap(policy, evidence, statements, decisions)
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, AnalysisError):
            raise
        raise AnalysisError("release_evidence_map_schema_invalid") from error
    if result.to_document() != dict(value):
        raise AnalysisError("release_evidence_map_authority_conflict")
    return result


def _evidence_from_document(value: object) -> ReleaseEvidence:
    document = _mapping(value, "evidence")
    if (
        set(document)
        != {
            "schema_version",
            "artifact_type",
            "scope",
            "evidence_class",
            "supported_statement",
            "exclusions",
            "terminal_status",
            "gate_status",
        }
        or document["schema_version"] != "1.0.0"
        or document["artifact_type"] != "release_evidence"
    ):
        raise AnalysisError("release_evidence_map_evidence_invalid")
    return ReleaseEvidence(
        _scope_from_document(document["scope"]),
        _string(document["evidence_class"], "evidence_class"),
        _string(document["supported_statement"], "supported_statement"),
        tuple(_string(item, "exclusion") for item in _list(document["exclusions"], "exclusions")),
        _string(document["terminal_status"], "terminal_status"),
        _string(document["gate_status"], "gate_status"),
    )


def _statement_from_document(value: object) -> ReleaseStatement:
    document = _mapping(value, "statement")
    if set(document) != {"statement_id", "statement_kind", "scope", "statement"}:
        raise AnalysisError("release_evidence_map_statement_invalid")
    return ReleaseStatement(
        _string(document["statement_id"], "statement_id"),
        _string(document["statement_kind"], "statement_kind"),
        _scope_from_document(document["scope"]),
        _string(document["statement"], "statement"),
    )


def _decision_from_document(value: object) -> ReleaseEvidenceDecision:
    document = _mapping(value, "decision")
    if set(document) != {
        "statement_id",
        "evidence_id",
        "evidence_sha256",
        "terminal_status",
        "supports",
        "does_not_support",
        "reasons",
        "decision_sha256",
    }:
        raise AnalysisError("release_evidence_map_decision_invalid")
    result = ReleaseEvidenceDecision(
        _string(document["statement_id"], "statement_id"),
        _string(document["evidence_id"], "evidence_id"),
        _string(document["evidence_sha256"], "evidence_sha256"),
        _string(document["terminal_status"], "terminal_status"),
        _string(document["supports"], "supports"),
        _string(document["does_not_support"], "does_not_support"),
        tuple(_string(item, "reason") for item in _list(document["reasons"], "reasons")),
    )
    if document["decision_sha256"] != result.decision_sha256:
        raise AnalysisError("release_evidence_map_decision_digest_invalid")
    return result


def _scope_from_document(value: object) -> ReleaseClaimScope:
    document = _mapping(value, "scope")
    expected = {
        "artifact_id",
        "artifact_sha256",
        "evidence_id",
        "path",
        "observation_mode",
        "configuration_sha256",
        "source_implementation_sha256",
        "runtime_lock_sha256",
        "version_sha256",
        "protocol_sha256",
        "population_id",
        "model_id",
        "stratum",
        "history_regime",
        "endpoint_id",
        "estimand_id",
        "source_sha256",
        "derivation_sha256",
        "reconciliation_sha256",
        "gate_id",
        "gate_sha256",
    }
    if set(document) != expected:
        raise AnalysisError("release_evidence_map_scope_invalid")
    normalized = dict(document)
    normalized["source_sha256"] = tuple(
        _string(item, "source_sha256") for item in _list(normalized["source_sha256"], "source")
    )
    return ReleaseClaimScope(**normalized)  # type: ignore[arg-type]


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AnalysisError(f"release_evidence_map_{name}_invalid")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise AnalysisError(f"release_evidence_map_{name}_invalid")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise AnalysisError(f"release_evidence_map_{name}_invalid")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise AnalysisError(f"release_evidence_map_{name}_invalid")
    return value


def _evaluate(
    evidence: ReleaseEvidence, statement: ReleaseStatement, policy: ReleaseMapPolicy
) -> ReleaseEvidenceDecision:
    reasons = _scope_conflicts(evidence.scope, statement.scope)
    if evidence.supported_statement != statement.statement:
        reasons.append("supported_statement_conflict")
    if evidence.terminal_status != "passed":
        status = {
            "null": "null",
            "harmful": "harmful",
            "indeterminate": "indeterminate",
        }.get(evidence.terminal_status, "blocked")
        reasons.append(f"evidence_{evidence.terminal_status}")
    elif evidence.gate_status != "passed":
        status = "blocked"
        reasons.append(f"gate_{evidence.gate_status}")
    elif reasons:
        status = "unqualified" if policy.allow_unqualified else "blocked"
    else:
        status = _classification_status(evidence, statement, reasons)
    return _decision(evidence, statement, status, reasons)


def _classification_status(
    evidence: ReleaseEvidence, statement: ReleaseStatement, reasons: list[str]
) -> str:
    if statement.statement_kind == "confirmatory_shipped_product_efficacy":
        if (
            evidence.evidence_class != "randomized_reconciled_product"
            or evidence.scope.stratum != "product"
        ):
            reasons.append("nonconfirmatory_evidence_cannot_promote_efficacy")
            return "blocked"
        return "positive"
    if statement.statement_kind == "randomized_treatment_estimand":
        if evidence.evidence_class != "randomized_reconciled_product":
            reasons.append("observational_randomized_confusion")
            return "blocked"
        return "positive"
    if statement.statement_kind == "observation_qualification":
        if evidence.evidence_class not in {
            "observation_sentinel",
            "observational_characterization",
        }:
            reasons.append("observation_evidence_class_required")
            return "blocked"
        return "estimation-only"
    if statement.statement_kind == "bounded_product_regression":
        if evidence.evidence_class not in {"fixture_retrieval", "fixture_roundtrip"}:
            reasons.append("fixture_regression_evidence_required")
            return "blocked"
        return "positive"
    raise AssertionError("validated statement kind")


def _scope_conflicts(evidence: ReleaseClaimScope, statement: ReleaseClaimScope) -> list[str]:
    fields = (
        "artifact_id",
        "artifact_sha256",
        "evidence_id",
        "path",
        "observation_mode",
        "configuration_sha256",
        "source_implementation_sha256",
        "runtime_lock_sha256",
        "version_sha256",
        "protocol_sha256",
        "population_id",
        "model_id",
        "stratum",
        "history_regime",
        "endpoint_id",
        "estimand_id",
        "source_sha256",
        "derivation_sha256",
        "reconciliation_sha256",
        "gate_id",
        "gate_sha256",
    )
    return [
        f"{field}_conflict"
        for field in fields
        if getattr(evidence, field) != getattr(statement, field)
    ]


def _decision_without_evidence(
    statement: ReleaseStatement, policy: ReleaseMapPolicy, reason: str
) -> ReleaseEvidenceDecision:
    return ReleaseEvidenceDecision(
        statement_id=statement.statement_id,
        evidence_id=statement.scope.evidence_id,
        evidence_sha256="0" * 64,
        terminal_status="unqualified" if policy.allow_unqualified else "blocked",
        supports=_supports(statement.statement),
        does_not_support=_does_not_support(()),
        reasons=(reason,),
    )


def _decision(
    evidence: ReleaseEvidence,
    statement: ReleaseStatement,
    status: str,
    reasons: list[str],
) -> ReleaseEvidenceDecision:
    return ReleaseEvidenceDecision(
        statement_id=statement.statement_id,
        evidence_id=evidence.scope.evidence_id,
        evidence_sha256=evidence.evidence_sha256,
        terminal_status=status,
        supports=_supports(evidence.supported_statement),
        does_not_support=_does_not_support(evidence.exclusions),
        reasons=tuple(reasons),
    )


def _supports(statement: str) -> str:
    return f"Supports only {statement}."


def _does_not_support(exclusions: tuple[str, ...]) -> str:
    values = tuple(sorted(set(_UNSUPPORTED_EXCLUSIONS) | set(exclusions)))
    return f"Does not support {', '.join(values)}."
