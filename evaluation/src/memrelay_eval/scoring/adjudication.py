"""Frozen, append-only adjudication for material blinded-judge disagreement."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactManifest, ArtifactRef
from memrelay_eval.domain.errors import (
    AdjudicationConformanceError,
    ArtifactIntegrityError,
    BlindingConformanceError,
    ConformancePauseError,
    JudgePanelConformanceError,
    ProcessLaunchError,
)
from memrelay_eval.domain.policies import require_treatment_neutral
from memrelay_eval.domain.ports import ArtifactStorePort, JudgeProcessPort
from memrelay_eval.evidence.required import require_unpaid_conformance_ports
from memrelay_eval.scoring.rubric import (
    JUDGE_CRITERIA,
    FrozenJudgeRubric,
    JudgeCriterionScore,
    JudgeLimits,
    JudgeRecord,
    JudgeRuntimeResult,
    JudgeSessionRequest,
    parse_blinded_view,
    require_citations_in_view,
)

ADJUDICATION_PROTOCOL_VERSION = "1.0.0"
ADJUDICATION_RECORD_SCHEMA_VERSION = "1.0.0"
_SHA256_LENGTH = 64
_MODEL_CAPABILITIES = (
    "tools",
    "permissions",
    "context",
    "events",
    "cancellation",
    "sessions",
)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class FrozenDisagreementThreshold:
    """One prospectively pinned strict score-spread threshold."""

    criterion: str
    maximum_spread: float

    def __post_init__(self) -> None:
        if (
            self.criterion not in JUDGE_CRITERIA
            or isinstance(self.maximum_spread, bool)
            or not isinstance(self.maximum_spread, (int, float))
            or not 0 <= self.maximum_spread <= 1
        ):
            raise AdjudicationConformanceError("adjudication_threshold_invalid")
        object.__setattr__(self, "maximum_spread", float(self.maximum_spread))

    def document(self) -> dict[str, object]:
        return {"criterion": self.criterion, "maximum_spread": self.maximum_spread}


@dataclass(frozen=True, slots=True)
class FrozenAdjudicationRubric:
    """Versioned, input-minimized rubric for the exceptional adjudicator session."""

    system_prompt: str = (
        "You are a blinded adjudicator. Resolve only the supplied disputed criteria from "
        "the blinded evidence and anonymous immutable rationale projections. Do not infer "
        "treatment, provider, credentials, cost, candidate identity, or judge identity."
    )
    tool_schemas: tuple[Mapping[str, object], ...] = field(
        default_factory=lambda: FrozenJudgeRubric().tool_schemas
    )
    decoding_controls: Mapping[str, object] = field(
        default_factory=lambda: FrozenJudgeRubric().decoding_controls
    )
    version: str = ADJUDICATION_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.version != ADJUDICATION_PROTOCOL_VERSION:
            raise AdjudicationConformanceError("adjudication_rubric_version_invalid")
        try:
            validated = FrozenJudgeRubric(
                self.system_prompt,
                self.tool_schemas,
                self.decoding_controls,
            )
        except JudgePanelConformanceError as error:
            raise AdjudicationConformanceError("adjudication_rubric_invalid") from error
        object.__setattr__(self, "tool_schemas", validated.tool_schemas)
        object.__setattr__(self, "decoding_controls", validated.decoding_controls)

    def document(self) -> dict[str, object]:
        return {
            "version": self.version,
            "system_prompt": self.system_prompt,
            "criteria": list(JUDGE_CRITERIA),
            "tool_schemas": [dict(tool) for tool in self.tool_schemas],
            "decoding_controls": dict(self.decoding_controls),
            "response_schema": "adjudication-record/1.0.0",
        }

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.document())).hexdigest()

    @property
    def system_prompt_sha256(self) -> str:
        return sha256(self.system_prompt.encode("utf-8")).hexdigest()

    @property
    def tools_sha256(self) -> str:
        return sha256(canonical_bytes([dict(tool) for tool in self.tool_schemas])).hexdigest()

    @property
    def decoding_controls_sha256(self) -> str:
        return sha256(canonical_bytes(dict(self.decoding_controls))).hexdigest()


@dataclass(frozen=True, slots=True)
class FrozenAdjudicationProtocol:
    """All adjudication discretion is frozen before any panel score is inspected."""

    thresholds: tuple[FrozenDisagreementThreshold, ...]
    adjudicator_model_id: str
    rubric: FrozenAdjudicationRubric
    limits: JudgeLimits
    presentation_order_sha256: str
    version: str = ADJUDICATION_PROTOCOL_VERSION
    rationale_order: tuple[int, int, int] = (1, 2, 3)

    def __post_init__(self) -> None:
        criteria = tuple(threshold.criterion for threshold in self.thresholds)
        if (
            self.version != ADJUDICATION_PROTOCOL_VERSION
            or set(criteria) != set(JUDGE_CRITERIA)
            or len(criteria) != len(JUDGE_CRITERIA)
            or not self.adjudicator_model_id
            or not _is_sha256(self.presentation_order_sha256)
            or self.limits.concurrency_limit != 1
            or tuple(sorted(self.rationale_order)) != (1, 2, 3)
            or self.presentation_order_sha256
            != sha256(canonical_bytes(list(self.rationale_order))).hexdigest()
            or self.limits.stage_token_limit < self.limits.per_session_tokens
            or self.limits.stage_tool_limit < self.limits.per_session_tools
            or self.limits.stage_active_seconds_limit < self.limits.per_session_active_seconds
            or self.limits.stage_wall_seconds_limit < self.limits.per_session_wall_seconds
        ):
            raise AdjudicationConformanceError("adjudication_protocol_invalid")
        object.__setattr__(self, "thresholds", tuple(self.thresholds))
        object.__setattr__(self, "rationale_order", tuple(self.rationale_order))

    def document(self) -> dict[str, object]:
        return {
            "version": self.version,
            "thresholds": [
                threshold.document()
                for threshold in sorted(self.thresholds, key=lambda item: item.criterion)
            ],
            "adjudicator_model_id": self.adjudicator_model_id,
            "rubric_sha256": self.rubric.sha256,
            "system_prompt_sha256": self.rubric.system_prompt_sha256,
            "tools_sha256": self.rubric.tools_sha256,
            "decoding_controls_sha256": self.rubric.decoding_controls_sha256,
            "limits": self.limits.document(),
            "rationale_order": list(self.rationale_order),
            "presentation_order_sha256": self.presentation_order_sha256,
            "response_schema": "adjudication-record/1.0.0",
        }

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.document())).hexdigest()


@dataclass(frozen=True, slots=True)
class ThresholdEvaluation:
    """Retained decision for one criterion, including the no-cross branch."""

    criterion: str
    maximum_spread: float | None
    observed_spread: float | None
    crossed: bool | None
    status: str
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if (
            self.criterion not in JUDGE_CRITERIA
            or self.status not in {"evaluated", "blocked"}
            or (self.status == "evaluated")
            != (
                self.maximum_spread is not None
                and self.observed_spread is not None
                and self.crossed is not None
                and self.failure_code is None
            )
            or (self.status == "blocked" and (self.crossed is not None or not self.failure_code))
        ):
            raise AdjudicationConformanceError("adjudication_threshold_evaluation_invalid")

    def document(self) -> dict[str, object]:
        result: dict[str, object] = {
            "criterion": self.criterion,
            "status": self.status,
            "maximum_spread": self.maximum_spread,
            "observed_spread": self.observed_spread,
            "crossed": self.crossed,
        }
        if self.failure_code is not None:
            result["failure_code"] = self.failure_code
        return result


@dataclass(frozen=True, slots=True)
class AdjudicationResolution:
    """The adjudicator's non-authoritative resolution for one disputed criterion."""

    score: float
    resolution: str
    uncertainty: float
    citations: tuple[str, ...]

    def __post_init__(self) -> None:
        JudgeCriterionScore(self.score, self.uncertainty, self.citations)
        if not isinstance(self.resolution, str) or not self.resolution.strip():
            raise AdjudicationConformanceError("adjudication_resolution_invalid")
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "uncertainty", float(self.uncertainty))
        object.__setattr__(self, "citations", tuple(self.citations))

    def document(self) -> dict[str, object]:
        return {
            "score": self.score,
            "resolution": self.resolution,
            "uncertainty": self.uncertainty,
            "citations": list(self.citations),
        }


@dataclass(frozen=True, slots=True)
class AdjudicationRecord:
    """Separate immutable evidence that never replaces source judge records."""

    candidate_id: str
    view_sha256: str
    panel_protocol_sha256: str
    adjudication_protocol_sha256: str
    source_judge_record_sha256: tuple[str, ...]
    threshold_evaluations: Mapping[str, ThresholdEvaluation]
    status: str
    runtime_lock_sha256: str | None = None
    model_lock_sha256: str | None = None
    rubric_sha256: str | None = None
    system_prompt_sha256: str | None = None
    tools_sha256: str | None = None
    decoding_controls_sha256: str | None = None
    presentation_order_sha256: str | None = None
    model_id: str | None = None
    model_family: str | None = None
    resolutions: Mapping[str, AdjudicationResolution] = field(default_factory=dict)
    failure_code: str | None = None
    schema_version: str = ADJUDICATION_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        pins = (
            self.runtime_lock_sha256,
            self.model_lock_sha256,
            self.rubric_sha256,
            self.system_prompt_sha256,
            self.tools_sha256,
            self.decoding_controls_sha256,
            self.presentation_order_sha256,
        )
        evaluations = dict(self.threshold_evaluations)
        resolutions = dict(self.resolutions)
        has_pins = any(value is not None for value in pins)
        if (
            self.schema_version != ADJUDICATION_RECORD_SCHEMA_VERSION
            or self.status not in {"not_triggered", "completed", "failed", "blocked", "unavailable"}
            or not self.candidate_id
            or not all(
                _is_sha256(value)
                for value in (
                    self.view_sha256,
                    self.panel_protocol_sha256,
                    self.adjudication_protocol_sha256,
                    *self.source_judge_record_sha256,
                )
            )
            or len(self.source_judge_record_sha256) > 3
            or len(set(self.source_judge_record_sha256)) != len(self.source_judge_record_sha256)
            or set(evaluations) != set(JUDGE_CRITERIA)
            or any(key != value.criterion for key, value in evaluations.items())
            or (
                has_pins
                and (
                    not all(_is_sha256(value) for value in pins)
                    or not self.model_id
                    or not self.model_family
                )
            )
            or (not has_pins and (self.model_id is not None or self.model_family is not None))
            or (self.status == "completed" and not has_pins)
            or (self.status == "completed" and len(self.source_judge_record_sha256) != 3)
            or (self.status == "completed")
            != (
                bool(resolutions)
                and set(resolutions)
                == {name for name, evaluation in evaluations.items() if evaluation.crossed}
                and self.failure_code is None
            )
            or (self.status != "completed" and (resolutions or not self.failure_code))
        ):
            raise AdjudicationConformanceError("adjudication_record_invalid")
        object.__setattr__(
            self, "source_judge_record_sha256", tuple(self.source_judge_record_sha256)
        )
        object.__setattr__(self, "threshold_evaluations", MappingProxyType(evaluations))
        object.__setattr__(self, "resolutions", MappingProxyType(resolutions))

    def document(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "view_sha256": self.view_sha256,
            "panel_protocol_sha256": self.panel_protocol_sha256,
            "adjudication_protocol_sha256": self.adjudication_protocol_sha256,
            "source_judge_record_sha256": list(self.source_judge_record_sha256),
            "threshold_evaluations": {
                name: self.threshold_evaluations[name].document() for name in JUDGE_CRITERIA
            },
            "status": self.status,
        }
        if any(
            value is not None
            for value in (
                self.runtime_lock_sha256,
                self.model_lock_sha256,
                self.rubric_sha256,
                self.system_prompt_sha256,
                self.tools_sha256,
                self.decoding_controls_sha256,
                self.presentation_order_sha256,
            )
        ):
            result["model"] = {"id": self.model_id, "family": self.model_family}
            result["pins"] = {
                "runtime_lock_sha256": self.runtime_lock_sha256,
                "model_lock_sha256": self.model_lock_sha256,
                "rubric_sha256": self.rubric_sha256,
                "system_prompt_sha256": self.system_prompt_sha256,
                "tools_sha256": self.tools_sha256,
                "decoding_controls_sha256": self.decoding_controls_sha256,
                "presentation_order_sha256": self.presentation_order_sha256,
            }
        if self.status == "completed":
            result["resolutions"] = {
                name: self.resolutions[name].document() for name in sorted(self.resolutions)
            }
        else:
            result["failure_code"] = self.failure_code
        return result

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.document())

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class AdjudicationOutcome:
    """Retained result and immutable artifacts for one adjudication authorization."""

    record: AdjudicationRecord
    record_artifact: ArtifactRef
    protocol_artifact: ArtifactRef

    @property
    def blocking_code(self) -> str | None:
        return (
            None
            if self.record.status in {"not_triggered", "completed"}
            else self.record.failure_code
        )


@dataclass(frozen=True, slots=True)
class _AdjudicatorModel:
    model_id: str
    family: str
    reasoning_effort: object
    context_tier: object
    model_lock_sha256: str


@dataclass(frozen=True, slots=True)
class _AdjudicationUsage:
    sessions: int = 0
    tokens: int = 0
    tools: int = 0
    active_seconds: float = 0.0
    wall_seconds: float = 0.0

    def can_authorize(self, limits: JudgeLimits) -> bool:
        return (
            self.sessions < limits.stage_session_limit
            and self.tokens + limits.per_session_tokens <= limits.stage_token_limit
            and self.tools + limits.per_session_tools <= limits.stage_tool_limit
            and self.active_seconds + limits.per_session_active_seconds
            <= limits.stage_active_seconds_limit
            and self.wall_seconds + limits.per_session_wall_seconds
            <= limits.stage_wall_seconds_limit
        )

    def plus(self, result: JudgeRuntimeResult) -> _AdjudicationUsage:
        return _AdjudicationUsage(
            self.sessions + 1,
            self.tokens + result.tokens,
            self.tools + result.tool_calls,
            self.active_seconds + result.active_seconds,
            self.wall_seconds + result.wall_seconds,
        )


def _select_adjudicator_model(
    model_lock: Mapping[str, object],
    runtime_lock_sha256: str,
    protocol: FrozenAdjudicationProtocol,
) -> _AdjudicatorModel | None:
    lock_sha256 = model_lock.get("lock_sha256")
    selected = model_lock.get("selected_models")
    if (
        not _is_sha256(lock_sha256)
        or model_lock.get("runtime_lock_sha256") != runtime_lock_sha256
        or not isinstance(selected, list)
    ):
        return None
    matches = [
        item
        for item in selected
        if isinstance(item, Mapping) and item.get("native_id") == protocol.adjudicator_model_id
    ]
    if len(matches) != 1:
        return None
    item = matches[0]
    capabilities = item.get("capabilities")
    family = item.get("family")
    if (
        item.get("role") != "judge"
        or not isinstance(family, str)
        or not family
        or not isinstance(capabilities, Mapping)
        or not all(
            capabilities.get(capability) not in {None, False, "unavailable"}
            for capability in _MODEL_CAPABILITIES
        )
    ):
        return None
    return _AdjudicatorModel(
        protocol.adjudicator_model_id,
        family,
        item.get("reasoning_effort", "unavailable"),
        item.get("context_tier", "unavailable"),
        lock_sha256,
    )


def _blocked_evaluations(code: str) -> dict[str, ThresholdEvaluation]:
    return {
        criterion: ThresholdEvaluation(criterion, None, None, None, "blocked", code)
        for criterion in JUDGE_CRITERIA
    }


def evaluate_disagreement_thresholds(
    records: Sequence[JudgeRecord], protocol: FrozenAdjudicationProtocol
) -> Mapping[str, ThresholdEvaluation]:
    """Evaluate every sealed criterion; malformed input cannot manufacture a threshold."""

    if (
        len(records) != 3
        or any(not isinstance(record, JudgeRecord) for record in records)
        or any(record.status != "completed" for record in records)
    ):
        return MappingProxyType(_blocked_evaluations("adjudication_judge_records_incomplete"))
    if (
        len({record.candidate_id for record in records}) != 1
        or len({record.view_sha256 for record in records}) != 1
        or len({record.panel_protocol_sha256 for record in records}) != 1
        or len({record.judge_slot for record in records}) != 3
    ):
        return MappingProxyType(_blocked_evaluations("adjudication_judge_records_malformed"))
    thresholds = {threshold.criterion: threshold for threshold in protocol.thresholds}
    if set(thresholds) != set(JUDGE_CRITERIA):
        return MappingProxyType(_blocked_evaluations("adjudication_thresholds_unsealed"))
    results: dict[str, ThresholdEvaluation] = {}
    for criterion in JUDGE_CRITERIA:
        scores = [record.criteria[criterion].score for record in records]
        spread = max(scores) - min(scores)
        threshold = thresholds[criterion].maximum_spread
        results[criterion] = ThresholdEvaluation(
            criterion,
            threshold,
            spread,
            spread > threshold,
            "evaluated",
        )
    return MappingProxyType(results)


def _parse_adjudication_response(
    response: Mapping[str, object], disputed: frozenset[str]
) -> Mapping[str, AdjudicationResolution]:
    resolutions = response.get("resolutions")
    if (
        set(response) != {"resolutions"}
        or not isinstance(resolutions, Mapping)
        or set(resolutions) != disputed
    ):
        raise AdjudicationConformanceError("adjudication_response_schema_invalid")
    parsed: dict[str, AdjudicationResolution] = {}
    for criterion in sorted(disputed):
        value = resolutions[criterion]
        if not isinstance(value, Mapping) or set(value) != {
            "score",
            "resolution",
            "uncertainty",
            "citations",
        }:
            raise AdjudicationConformanceError("adjudication_response_resolution_invalid")
        citations = value["citations"]
        if not isinstance(citations, Sequence) or isinstance(citations, (str, bytes)):
            raise AdjudicationConformanceError("adjudication_response_citations_invalid")
        parsed[criterion] = AdjudicationResolution(
            value["score"], value["resolution"], value["uncertainty"], tuple(citations)
        )
    return MappingProxyType(parsed)


def _require_adjudication_citations(
    resolutions: Mapping[str, AdjudicationResolution], view: Mapping[str, object]
) -> None:
    require_citations_in_view(
        {
            name: JudgeCriterionScore(value.score, value.uncertainty, value.citations)
            for name, value in resolutions.items()
        },
        view,
    )


class FrozenDisagreementAdjudicator:
    """Authorize exactly one fresh blinded Copilot session for a retained tuple."""

    def __init__(
        self,
        store: ArtifactStorePort,
        judge_process: JudgeProcessPort,
        model_lock: Mapping[str, object],
        runtime_lock_sha256: str,
        protocol: FrozenAdjudicationProtocol,
    ) -> None:
        require_unpaid_conformance_ports(store)
        if not _is_sha256(runtime_lock_sha256):
            raise AdjudicationConformanceError("adjudication_runtime_lock_invalid")
        self._store = store
        self._judge_process = judge_process
        self._model_lock = dict(model_lock)
        self._runtime_lock_sha256 = runtime_lock_sha256
        self._protocol = protocol
        self._outcomes: dict[tuple[str, str, str, str], AdjudicationOutcome] = {}
        self._source_bindings: dict[tuple[str, str, str, str], tuple[str, ...]] = {}
        self._usage = _AdjudicationUsage()
        self._lock = asyncio.Lock()

    async def adjudicate(
        self,
        candidate_id: str,
        view: ArtifactRef,
        records: Sequence[JudgeRecord],
        *,
        executable_passed: bool = True,
        categorical_blockers: Sequence[str] = (),
        evidence_available: bool = True,
    ) -> AdjudicationOutcome:
        """Append terminal evidence; replay returns it and no path retries or substitutes."""

        require_treatment_neutral(candidate_id)
        source_hashes = (
            tuple(record.sha256 for record in records)
            if all(isinstance(record, JudgeRecord) for record in records)
            else ()
        )
        panel_sha = (
            records[0].panel_protocol_sha256
            if records and isinstance(records[0], JudgeRecord)
            else "0" * 64
        )
        key = (candidate_id, view.sha256, panel_sha, self._protocol.sha256)
        async with self._lock:
            prior = self._outcomes.get(key)
            if prior is not None:
                if self._source_bindings[key] != source_hashes:
                    raise AdjudicationConformanceError("adjudication_authorization_conflict")
                return prior
            outcome = await self._adjudicate_once(
                candidate_id,
                view,
                records,
                source_hashes,
                executable_passed,
                tuple(categorical_blockers),
                evidence_available,
            )
            self._outcomes[key] = outcome
            self._source_bindings[key] = source_hashes
            return outcome

    async def _adjudicate_once(
        self,
        candidate_id: str,
        view: ArtifactRef,
        records: Sequence[JudgeRecord],
        source_hashes: tuple[str, ...],
        executable_passed: bool,
        categorical_blockers: tuple[str, ...],
        evidence_available: bool,
    ) -> AdjudicationOutcome:
        evaluations = evaluate_disagreement_thresholds(records, self._protocol)
        panel_sha = (
            records[0].panel_protocol_sha256
            if records and isinstance(records[0], JudgeRecord)
            else "0" * 64
        )
        protocol_artifact = self._store.put_bytes(
            canonical_bytes(self._protocol.document()),
            media_type="application/json",
            classification="unpaid_conformance",
        )
        failure: str | None = None
        status = "blocked"
        if not all(item.status == "evaluated" for item in evaluations.values()):
            failure = next(item.failure_code for item in evaluations.values() if item.failure_code)
        elif any(record.candidate_id != candidate_id for record in records) or any(
            record.view_sha256 != view.sha256 for record in records
        ):
            failure = "adjudication_source_binding_mismatch"
        elif not any(item.crossed for item in evaluations.values()):
            status, failure = "not_triggered", "adjudication_not_triggered"
        elif not executable_passed:
            failure = "adjudication_executable_authority_blocked"
        elif categorical_blockers:
            failure = "adjudication_categorical_blocker"
        elif not evidence_available:
            failure = "adjudication_evidence_unavailable"
        else:
            try:
                view_bytes = self._store.open_verified(view)
                blinded_view = parse_blinded_view(view_bytes)
                authorized_artifacts = _authorized_blinded_artifacts(blinded_view)
            except (
                AdjudicationConformanceError,
                ArtifactIntegrityError,
                BlindingConformanceError,
                JudgePanelConformanceError,
            ):
                failure = "adjudication_view_unavailable"
            else:
                model = _select_adjudicator_model(
                    self._model_lock, self._runtime_lock_sha256, self._protocol
                )
                if model is None:
                    failure = "adjudication_model_unavailable"
                else:
                    return await self._run(
                        candidate_id,
                        view,
                        records,
                        source_hashes,
                        evaluations,
                        blinded_view,
                        authorized_artifacts,
                        model,
                        protocol_artifact,
                    )
        record = AdjudicationRecord(
            candidate_id,
            view.sha256,
            panel_sha,
            self._protocol.sha256,
            source_hashes,
            evaluations,
            status,
            failure_code=failure,
        )
        return self._store_outcome(record, protocol_artifact)

    async def _run(
        self,
        candidate_id: str,
        view: ArtifactRef,
        records: Sequence[JudgeRecord],
        source_hashes: tuple[str, ...],
        evaluations: Mapping[str, ThresholdEvaluation],
        blinded_view: Mapping[str, object],
        authorized_artifacts: Mapping[str, str],
        model: _AdjudicatorModel,
        protocol_artifact: ArtifactRef,
    ) -> AdjudicationOutcome:
        disputed = tuple(name for name in JUDGE_CRITERIA if evaluations[name].crossed)
        request_view = _adjudication_view(
            blinded_view, records, disputed, self._protocol.rationale_order
        )
        request = JudgeSessionRequest(
            session_id=f"{self._protocol.sha256[:16]}-{candidate_id}-adjudication",
            candidate_id=candidate_id,
            model_id=model.model_id,
            reasoning_effort=model.reasoning_effort,
            context_tier=model.context_tier,
            system_prompt=self._protocol.rubric.system_prompt,
            rubric_sha256=self._protocol.rubric.sha256,
            tools=self._protocol.rubric.tool_schemas,
            decoding_controls=self._protocol.rubric.decoding_controls,
            view_bytes=request_view,
            wall_seconds_limit=self._protocol.limits.per_session_wall_seconds,
            authorized_blinded_artifacts=authorized_artifacts,
            response_contract="adjudication",
            disputed_criteria=disputed,
        )
        failure: str | None = None
        resolutions: Mapping[str, AdjudicationResolution] = {}
        if not self._usage.can_authorize(self._protocol.limits):
            failure = "adjudication_stage_cap_exhausted"
        else:
            try:
                result = await self._judge_process.run_judge_session(request)
            except (ConformancePauseError, JudgePanelConformanceError, ProcessLaunchError) as error:
                failure = getattr(error, "code", "adjudication_process_launch_failed")
                self._usage = self._usage.plus(
                    JudgeRuntimeResult("failed", None, failure_code=failure)
                )
            else:
                if not isinstance(result, JudgeRuntimeResult):
                    failure = "adjudication_runtime_result_invalid"
                    self._usage = self._usage.plus(
                        JudgeRuntimeResult("failed", None, failure_code=failure)
                    )
                else:
                    self._usage = self._usage.plus(result)
                    failure = _limit_failure(result, self._usage, self._protocol.limits)
                    if failure is None and result.status != "completed":
                        failure = result.failure_code or "adjudication_runtime_failed"
                    elif failure is None:
                        try:
                            resolutions = _parse_adjudication_response(
                                result.response or {}, frozenset(disputed)
                            )
                            _require_adjudication_citations(resolutions, blinded_view)
                        except (AdjudicationConformanceError, JudgePanelConformanceError):
                            failure = "adjudication_response_invalid"
        if failure is not None:
            record = AdjudicationRecord(
                candidate_id,
                view.sha256,
                records[0].panel_protocol_sha256,
                self._protocol.sha256,
                source_hashes,
                evaluations,
                "failed",
                self._runtime_lock_sha256,
                model.model_lock_sha256,
                self._protocol.rubric.sha256,
                self._protocol.rubric.system_prompt_sha256,
                self._protocol.rubric.tools_sha256,
                self._protocol.rubric.decoding_controls_sha256,
                self._protocol.presentation_order_sha256,
                model.model_id,
                model.family,
                failure_code=failure,
            )
        else:
            record = AdjudicationRecord(
                candidate_id,
                view.sha256,
                records[0].panel_protocol_sha256,
                self._protocol.sha256,
                source_hashes,
                evaluations,
                "completed",
                self._runtime_lock_sha256,
                model.model_lock_sha256,
                self._protocol.rubric.sha256,
                self._protocol.rubric.system_prompt_sha256,
                self._protocol.rubric.tools_sha256,
                self._protocol.rubric.decoding_controls_sha256,
                self._protocol.presentation_order_sha256,
                model.model_id,
                model.family,
                resolutions,
            )
        return self._store_outcome(record, protocol_artifact)

    def _store_outcome(
        self, record: AdjudicationRecord, protocol_artifact: ArtifactRef
    ) -> AdjudicationOutcome:
        artifact = self._store.put_bytes(
            record.canonical_bytes,
            media_type="application/json",
            classification="unpaid_conformance",
        )
        return AdjudicationOutcome(record, artifact, protocol_artifact)


def _limit_failure(
    result: JudgeRuntimeResult, usage: _AdjudicationUsage, limits: JudgeLimits
) -> str | None:
    if (
        result.tokens > limits.per_session_tokens
        or result.tool_calls > limits.per_session_tools
        or result.active_seconds > limits.per_session_active_seconds
        or result.wall_seconds > limits.per_session_wall_seconds
    ):
        return "adjudication_session_cap_exceeded"
    if (
        usage.sessions > limits.stage_session_limit
        or usage.tokens > limits.stage_token_limit
        or usage.tools > limits.stage_tool_limit
        or usage.active_seconds > limits.stage_active_seconds_limit
        or usage.wall_seconds > limits.stage_wall_seconds_limit
    ):
        return "adjudication_stage_cap_exceeded"
    return None


def _adjudication_view(
    view: Mapping[str, object],
    records: Sequence[JudgeRecord],
    disputed: Sequence[str],
    rationale_order: Sequence[int],
) -> bytes:
    """Build the sole input projection: blinded evidence plus anonymous immutable outputs."""

    records_by_slot = {record.judge_slot: record for record in records}
    rationales: dict[str, list[dict[str, object]]] = {}
    for criterion in disputed:
        rationales[criterion] = [
            {
                "rationale_id": f"rationale-{position}",
                "score": record.criteria[criterion].score,
                "uncertainty": record.criteria[criterion].uncertainty,
                "citations": list(record.criteria[criterion].citations),
            }
            for position, slot in enumerate(rationale_order, start=1)
            for record in (records_by_slot[slot],)
        ]
    return canonical_bytes(
        {
            "schema_version": "1.0.0",
            "blinded_evidence": {
                "evidence": view["evidence"],
                "artifact_locations": view["artifact_locations"],
            },
            "disputed_criteria": list(disputed),
            "anonymized_judge_rationales": rationales,
        }
    )


def _authorized_blinded_artifacts(view: Mapping[str, object]) -> Mapping[str, str]:
    evidence = view.get("evidence")
    locations = view.get("artifact_locations")
    if not isinstance(evidence, Mapping) or not isinstance(locations, Mapping):
        raise AdjudicationConformanceError("adjudication_view_not_blinded")
    artifacts: dict[str, str] = {}
    for name, location in locations.items():
        if not isinstance(name, str) or not isinstance(location, str):
            raise AdjudicationConformanceError("adjudication_view_not_blinded")
        artifacts[location] = canonical_bytes(
            {"artifact_location": location, "evidence": evidence.get(name)}
        ).decode("utf-8")
    return artifacts


def write_adjudication_record_manifest(
    store: ArtifactStorePort,
    record: AdjudicationRecord,
    record_artifact: ArtifactRef,
    protocol_artifact: ArtifactRef,
    view_artifact: ArtifactRef,
    source_judge_artifacts: Sequence[ArtifactRef],
    manifest: ArtifactManifest,
) -> ArtifactRef:
    """Write an append-only manifest that links, but never rewrites, judge records."""

    require_unpaid_conformance_ports(store)
    expected_sources = {
        protocol_artifact.artifact_id,
        view_artifact.artifact_id,
        *(item.artifact_id for item in source_judge_artifacts),
    }
    if (
        manifest.kind != "adjudication_record"
        or manifest.artifact_id != record_artifact.artifact_id
        or manifest.sha256 != record.sha256
        or manifest.size_bytes != record_artifact.size_bytes
        or set(manifest.source_artifact_ids) != expected_sources
        or len(source_judge_artifacts) != 3
        or {item.sha256 for item in source_judge_artifacts}
        != set(record.source_judge_record_sha256)
    ):
        raise AdjudicationConformanceError("adjudication_manifest_provenance_mismatch")
    return store.write_manifest(manifest)
