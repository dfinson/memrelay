"""Separate, fail-closed normalization of executable and qualitative outcomes."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import GraderResult
from memrelay_eval.domain.errors import JudgePanelConformanceError
from memrelay_eval.domain.states import GraderTerminalKind
from memrelay_eval.scoring.adjudication import AdjudicationRecord
from memrelay_eval.scoring.reliability import PanelGateEvidence
from memrelay_eval.scoring.rubric import JUDGE_CRITERIA, JudgeRecord

ENDPOINT_RECORD_SCHEMA_VERSION = "1.0.0"
OUTCOME_NORMALIZATION_VERSION = "1.0.0"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_CATEGORICAL_KINDS = frozenset(
    {"security", "governance", "evidence_integrity", "grading", "causal_validity"}
)
_ENDPOINT_STATUSES = frozenset({"passed", "failed", "unavailable", "blocked"})


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _finite_unit(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) and 0 <= normalized <= 1 else None


@dataclass(frozen=True, slots=True)
class FrozenQualitativeAggregation:
    """The sole pre-outcome scale, weights, and missingness rule for panel quality."""

    criterion_weights: Mapping[str, float]
    pass_threshold: float
    missingness_policy: str = "block"
    version: str = OUTCOME_NORMALIZATION_VERSION

    def __post_init__(self) -> None:
        weights = dict(self.criterion_weights)
        normalized = {name: _finite_unit(value) for name, value in weights.items()}
        threshold = _finite_unit(self.pass_threshold)
        if (
            self.version != OUTCOME_NORMALIZATION_VERSION
            or set(weights) != set(JUDGE_CRITERIA)
            or any(value is None or value == 0 for value in normalized.values())
            or not math.isclose(
                sum(value for value in normalized.values() if value is not None),
                1.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or threshold is None
            or self.missingness_policy != "block"
        ):
            raise JudgePanelConformanceError("qualitative_aggregation_protocol_invalid")
        normalized_weights: dict[str, float] = {}
        for criterion in JUDGE_CRITERIA:
            value = normalized[criterion]
            if value is None:
                raise JudgePanelConformanceError("qualitative_aggregation_protocol_invalid")
            normalized_weights[criterion] = value
        object.__setattr__(
            self,
            "criterion_weights",
            MappingProxyType(normalized_weights),
        )
        object.__setattr__(self, "pass_threshold", threshold)

    def document(self) -> dict[str, object]:
        return {
            "version": self.version,
            "criterion_weights": {
                criterion: self.criterion_weights[criterion] for criterion in JUDGE_CRITERIA
            },
            "pass_threshold": self.pass_threshold,
            "missingness_policy": self.missingness_policy,
        }

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.document())).hexdigest()


@dataclass(frozen=True, slots=True)
class CategoricalBlockerRecord:
    """Immutable categorical blocker evidence that remains distinct from endpoints."""

    category: str
    code: str
    source_evidence_sha256: tuple[str, ...]
    schema_version: str = ENDPOINT_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        evidence = tuple(sorted(set(self.source_evidence_sha256)))
        if (
            self.schema_version != ENDPOINT_RECORD_SCHEMA_VERSION
            or self.category not in _CATEGORICAL_KINDS
            or not isinstance(self.code, str)
            or not self.code
            or not evidence
            or any(not _is_sha256(value) for value in evidence)
        ):
            raise JudgePanelConformanceError("categorical_blocker_record_invalid")
        object.__setattr__(self, "source_evidence_sha256", evidence)

    def document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "category": self.category,
            "code": self.code,
            "source_evidence_sha256": list(self.source_evidence_sha256),
        }

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.document())).hexdigest()


@dataclass(frozen=True, slots=True)
class EndpointRecord:
    """One normalized endpoint that cannot carry authority from the other endpoint."""

    candidate_id: str
    endpoint: str
    authority: str
    status: str
    value: bool | float | None
    unavailable_reason: str | None
    scorer_sha256: str
    rubric_sha256: str | None
    grader_sha256: str | None
    snapshot_sha256: str | None
    protocol_sha256: str | None
    source_evidence_sha256: tuple[str, ...]
    derivation_sha256: str | None = None
    schema_version: str = ENDPOINT_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        evidence = tuple(sorted(set(self.source_evidence_sha256)))
        expected_authority = {
            "hard": "deterministic_grader",
            "qualitative": "blinded_judge_panel",
        }.get(self.endpoint)
        valid_value = (
            isinstance(self.value, bool)
            if self.endpoint == "hard"
            else _finite_unit(self.value) is not None
        )
        if (
            self.schema_version != ENDPOINT_RECORD_SCHEMA_VERSION
            or not self.candidate_id
            or expected_authority != self.authority
            or self.status not in _ENDPOINT_STATUSES
            or not _is_sha256(self.scorer_sha256)
            or any(
                value is not None and not _is_sha256(value)
                for value in (
                    self.rubric_sha256,
                    self.grader_sha256,
                    self.snapshot_sha256,
                    self.protocol_sha256,
                )
            )
            or not evidence
            or any(not _is_sha256(value) for value in evidence)
            or (
                self.status in {"passed", "failed"}
                and (not valid_value or self.unavailable_reason is not None)
            )
            or (
                self.status in {"unavailable", "blocked"}
                and (self.value is not None or not self.unavailable_reason)
            )
        ):
            raise JudgePanelConformanceError("endpoint_record_invalid")
        if self.endpoint == "qualitative" and self.value is not None:
            object.__setattr__(self, "value", float(self.value))
        object.__setattr__(self, "source_evidence_sha256", evidence)
        computed = sha256(canonical_bytes(self._document_without_derivation())).hexdigest()
        if self.derivation_sha256 is not None and self.derivation_sha256 != computed:
            raise JudgePanelConformanceError("endpoint_derivation_hash_mismatch")
        object.__setattr__(self, "derivation_sha256", computed)

    def _document_without_derivation(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "endpoint": self.endpoint,
            "authority": self.authority,
            "status": self.status,
            "value": self.value,
            "unavailable_reason": self.unavailable_reason,
            "scorer_sha256": self.scorer_sha256,
            "rubric_sha256": self.rubric_sha256,
            "grader_sha256": self.grader_sha256,
            "snapshot_sha256": self.snapshot_sha256,
            "protocol_sha256": self.protocol_sha256,
            "source_evidence_sha256": list(self.source_evidence_sha256),
        }

    def document(self) -> dict[str, object]:
        return {**self._document_without_derivation(), "derivation_sha256": self.derivation_sha256}


@dataclass(frozen=True, slots=True)
class NormalizedOutcomes:
    """Immutable multi-record state for reconciliation; endpoint authority never pools."""

    hard: EndpointRecord
    qualitative: EndpointRecord
    categorical_blockers: tuple[CategoricalBlockerRecord, ...]
    normalization_codes: tuple[str, ...]
    artifact_authority: str
    schema_version: str = OUTCOME_NORMALIZATION_VERSION

    def __post_init__(self) -> None:
        blockers = tuple(sorted(self.categorical_blockers, key=lambda item: item.sha256))
        codes = tuple(sorted(set(self.normalization_codes)))
        if (
            self.schema_version != OUTCOME_NORMALIZATION_VERSION
            or self.hard.endpoint != "hard"
            or self.qualitative.endpoint != "qualitative"
            or self.hard.candidate_id != self.qualitative.candidate_id
            or self.artifact_authority != "unpaid_conformance"
        ):
            raise JudgePanelConformanceError("normalized_outcomes_invalid")
        object.__setattr__(self, "categorical_blockers", blockers)
        object.__setattr__(self, "normalization_codes", codes)

    @property
    def eligible_for_claims(self) -> bool:
        return (
            self.hard.status == "passed"
            and self.qualitative.status == "passed"
            and not self.categorical_blockers
            and not self.normalization_codes
        )

    @property
    def eligible_for_paid_or_study(self) -> bool:
        """Story 4 durable adapters and reconciliation are still required."""
        return False

    @property
    def authority_conflict(self) -> bool:
        return self.qualitative.status == "passed" and (
            self.hard.status != "passed" or bool(self.categorical_blockers)
        )

    def document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "hard_endpoint": self.hard.document(),
            "qualitative_endpoint": self.qualitative.document(),
            "categorical_blockers": [item.document() for item in self.categorical_blockers],
            "normalization_codes": list(self.normalization_codes),
            "authority_conflict": self.authority_conflict,
            "eligible_for_claims": self.eligible_for_claims,
            "artifact_authority": self.artifact_authority,
            "eligible_for_paid_or_study": self.eligible_for_paid_or_study,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.document())

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes).hexdigest()


def normalize_outcomes(
    candidate_id: str,
    grader: GraderResult | None,
    judge_records: Sequence[JudgeRecord] | Sequence[object],
    panel_gate: PanelGateEvidence | None,
    adjudication: AdjudicationRecord | None,
    aggregation: FrozenQualitativeAggregation | None,
    *,
    categorical_blockers: Sequence[CategoricalBlockerRecord] | Sequence[object] = (),
    artifact_authority: str = "unpaid_conformance",
) -> NormalizedOutcomes:
    """Normalize independent authorities without calls, substitutions, or implicit defaults."""

    codes: list[str] = []
    blockers = _valid_blockers(categorical_blockers, codes)
    if not isinstance(candidate_id, str) or not candidate_id:
        raise JudgePanelConformanceError("outcome_candidate_id_invalid")
    if artifact_authority != "unpaid_conformance":
        codes.append("outcome_artifact_authority_unauthorized")

    hard = _hard_endpoint(candidate_id, grader, artifact_authority, codes)
    qualitative = _qualitative_endpoint(
        candidate_id,
        judge_records,
        panel_gate,
        adjudication,
        aggregation,
        artifact_authority,
        codes,
    )
    return NormalizedOutcomes(
        hard,
        qualitative,
        tuple(blockers),
        tuple(codes),
        artifact_authority="unpaid_conformance",
    )


def _valid_blockers(
    blockers: Sequence[CategoricalBlockerRecord] | Sequence[object], codes: list[str]
) -> list[CategoricalBlockerRecord]:
    if not all(isinstance(item, CategoricalBlockerRecord) for item in blockers):
        codes.append("categorical_blocker_evidence_malformed")
        return []
    return [item for item in blockers if isinstance(item, CategoricalBlockerRecord)]


def _hard_endpoint(
    candidate_id: str,
    grader: GraderResult | None,
    artifact_authority: str,
    codes: list[str],
) -> EndpointRecord:
    if artifact_authority != "unpaid_conformance":
        return _unavailable_hard(candidate_id, "outcome_artifact_authority_unauthorized")
    if not isinstance(grader, GraderResult):
        codes.append("grader_evidence_missing_or_malformed")
        return _unavailable_hard(candidate_id, "grader_evidence_missing_or_malformed")

    status = {
        GraderTerminalKind.PASSED: "passed",
        GraderTerminalKind.FAILED: "failed",
        GraderTerminalKind.UNAVAILABLE: "unavailable",
        GraderTerminalKind.BLOCKED: "blocked",
    }[grader.terminal]
    reason = None if status in {"passed", "failed"} else f"grader_{grader.terminal.value}"
    evidence = tuple(
        item.sha256
        for item in (grader.result_artifact, grader.raw_output_artifact, *grader.evidence_refs)
        if item is not None
    )
    return EndpointRecord(
        candidate_id,
        "hard",
        "deterministic_grader",
        status,
        grader.binary_passed,
        reason,
        grader.contract_sha256,
        None,
        grader.contract_sha256,
        grader.snapshot_sha256,
        None,
        evidence,
    )


def _unavailable_hard(candidate_id: str, reason: str) -> EndpointRecord:
    evidence = sha256(reason.encode("ascii")).hexdigest()
    return EndpointRecord(
        candidate_id,
        "hard",
        "deterministic_grader",
        "unavailable",
        None,
        reason,
        evidence,
        None,
        None,
        None,
        None,
        (evidence,),
    )


def _qualitative_endpoint(
    candidate_id: str,
    raw_records: Sequence[JudgeRecord] | Sequence[object],
    panel_gate: PanelGateEvidence | None,
    adjudication: AdjudicationRecord | None,
    aggregation: FrozenQualitativeAggregation | None,
    artifact_authority: str,
    codes: list[str],
) -> EndpointRecord:
    if artifact_authority != "unpaid_conformance":
        return _unavailable_qualitative(
            candidate_id, "outcome_artifact_authority_unauthorized", (), None, None
        )
    if not isinstance(aggregation, FrozenQualitativeAggregation):
        codes.append("qualitative_aggregation_protocol_missing_or_malformed")
        return _unavailable_qualitative(
            candidate_id, "qualitative_aggregation_protocol_missing_or_malformed", (), None, None
        )
    if not all(isinstance(record, JudgeRecord) for record in raw_records):
        codes.append("panel_records_missing_or_malformed")
        return _unavailable_qualitative(
            candidate_id, "panel_records_missing_or_malformed", (), aggregation.sha256, None
        )
    records = tuple(raw_records)  # type: ignore[assignment]
    records_digest = sha256(
        canonical_bytes(
            [
                record.document()
                for record in sorted(records, key=lambda item: (item.candidate_id, item.judge_slot))
            ]
        )
    ).hexdigest()
    candidate_records = tuple(record for record in records if record.candidate_id == candidate_id)
    evidence = tuple(record.sha256 for record in candidate_records)
    if not isinstance(panel_gate, PanelGateEvidence):
        codes.append("panel_gate_missing_or_malformed")
        return _unavailable_qualitative(
            candidate_id, "panel_gate_missing_or_malformed", evidence, aggregation.sha256, None
        )
    evidence = (*evidence, panel_gate.sha256)
    record_reason = _validate_panel_records(
        candidate_id, candidate_records, panel_gate, records_digest
    )
    if record_reason is not None:
        codes.append(record_reason)
        return _unavailable_qualitative(
            candidate_id,
            record_reason,
            evidence,
            aggregation.sha256,
            panel_gate.panel_protocol_sha256,
        )
    if not panel_gate.reliability_passed:
        codes.append("panel_reliability_gate_failed_or_unavailable")
        return _blocked_qualitative(
            candidate_id,
            "panel_reliability_gate_failed_or_unavailable",
            evidence,
            aggregation.sha256,
            candidate_records[0].rubric_sha256,
            panel_gate.panel_protocol_sha256,
        )
    adjudication_reason, adjudicated_scores, adjudication_hash = _resolve_adjudication(
        candidate_id, candidate_records, adjudication, panel_gate.panel_protocol_sha256
    )
    if adjudication_hash is not None:
        evidence = (*evidence, adjudication_hash)
    if adjudication_reason is not None:
        codes.append(adjudication_reason)
        return _blocked_qualitative(
            candidate_id,
            adjudication_reason,
            evidence,
            aggregation.sha256,
            candidate_records[0].rubric_sha256,
            panel_gate.panel_protocol_sha256,
        )
    score = sum(
        aggregation.criterion_weights[criterion]
        * adjudicated_scores.get(
            criterion,
            sum(record.criteria[criterion].score for record in candidate_records)
            / len(candidate_records),
        )
        for criterion in JUDGE_CRITERIA
    )
    status = "passed" if score >= aggregation.pass_threshold else "failed"
    return EndpointRecord(
        candidate_id,
        "qualitative",
        "blinded_judge_panel",
        status,
        score,
        None,
        aggregation.sha256,
        candidate_records[0].rubric_sha256,
        None,
        None,
        panel_gate.panel_protocol_sha256,
        evidence,
    )


def _validate_panel_records(
    candidate_id: str,
    records: tuple[JudgeRecord, ...],
    panel_gate: PanelGateEvidence,
    records_digest: str,
) -> str | None:
    if (
        len(records) != 3
        or {record.judge_slot for record in records} != {1, 2, 3}
        or any(record.status != "completed" for record in records)
        or len({record.view_sha256 for record in records}) != 1
        or len({record.panel_protocol_sha256 for record in records}) != 1
        or len({record.rubric_sha256 for record in records}) != 1
        or panel_gate.records_sha256 != records_digest
        or panel_gate.panel_protocol_sha256 != records[0].panel_protocol_sha256
        or any(record.candidate_id != candidate_id for record in records)
    ):
        return "panel_records_incomplete_or_conflicting"
    return None


def _resolve_adjudication(
    candidate_id: str,
    records: tuple[JudgeRecord, ...],
    adjudication: AdjudicationRecord | None,
    panel_protocol_sha256: str | None,
) -> tuple[str | None, Mapping[str, float], str | None]:
    if not isinstance(adjudication, AdjudicationRecord):
        return "adjudication_missing_or_malformed", {}, None
    if (
        adjudication.candidate_id != candidate_id
        or adjudication.panel_protocol_sha256 != panel_protocol_sha256
        or set(adjudication.source_judge_record_sha256) != {record.sha256 for record in records}
    ):
        return "adjudication_source_binding_mismatch", {}, adjudication.sha256
    if adjudication.status == "not_triggered":
        if any(
            item.status != "evaluated" or item.crossed is not False
            for item in adjudication.threshold_evaluations.values()
        ):
            return "adjudication_not_triggered_evidence_invalid", {}, adjudication.sha256
        return None, {}, adjudication.sha256
    if adjudication.status != "completed":
        return f"adjudication_{adjudication.status}", {}, adjudication.sha256
    crossed = {
        name
        for name, evaluation in adjudication.threshold_evaluations.items()
        if evaluation.crossed
    }
    if not crossed or set(adjudication.resolutions) != crossed:
        return "adjudication_resolution_incomplete", {}, adjudication.sha256
    return (
        None,
        {name: resolution.score for name, resolution in adjudication.resolutions.items()},
        adjudication.sha256,
    )


def _unavailable_qualitative(
    candidate_id: str,
    reason: str,
    evidence: Sequence[str],
    scorer_sha256: str | None,
    protocol_sha256: str | None,
) -> EndpointRecord:
    fallback = sha256(reason.encode("ascii")).hexdigest()
    return EndpointRecord(
        candidate_id,
        "qualitative",
        "blinded_judge_panel",
        "unavailable",
        None,
        reason,
        scorer_sha256 or fallback,
        None,
        None,
        None,
        protocol_sha256,
        (*evidence, fallback),
    )


def _blocked_qualitative(
    candidate_id: str,
    reason: str,
    evidence: Sequence[str],
    scorer_sha256: str,
    rubric_sha256: str,
    protocol_sha256: str | None,
) -> EndpointRecord:
    return EndpointRecord(
        candidate_id,
        "qualitative",
        "blinded_judge_panel",
        "blocked",
        None,
        reason,
        scorer_sha256,
        rubric_sha256,
        None,
        None,
        protocol_sha256,
        tuple(evidence),
    )
