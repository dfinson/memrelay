"""Deterministic executable-grading policy without assignment access."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import (
    ArtifactManifest,
    ArtifactRef,
    FlakyTestClassification,
    GraderContract,
    GraderResult,
)
from memrelay_eval.domain.errors import (
    ArtifactIntegrityError,
    BlindingConformanceError,
    ConformancePauseError,
    GraderContractError,
    GraderReplayMismatchError,
    JudgePanelConformanceError,
    ProcessLaunchError,
)
from memrelay_eval.domain.policies import require_treatment_neutral
from memrelay_eval.domain.ports import ArtifactStorePort, GraderPort, JudgeProcessPort
from memrelay_eval.domain.states import GraderTerminalKind
from memrelay_eval.evidence.required import require_unpaid_conformance_ports
from memrelay_eval.scoring.calibration import FrozenPanelQualificationProtocol
from memrelay_eval.scoring.rubric import (
    FrozenJudgeRubric,
    FrozenPanelSchedule,
    JudgeLimits,
    JudgeRecord,
    JudgeRuntimeResult,
    JudgeSessionRequest,
    parse_blinded_view,
    parse_judge_response,
    require_citations_in_view,
)

CONTINUOUS_SCORE_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class GradingReplayComparison:
    """Timing-excluded deterministic replay verdict for two immutable results."""

    matches: bool
    mismatches: tuple[str, ...]


def compare_grading_replays(first: GraderResult, second: GraderResult) -> GradingReplayComparison:
    """Compare only authoritative result fields; timing remains evidence, not outcome."""
    mismatches: list[str] = []
    if first.snapshot_sha256 != second.snapshot_sha256:
        mismatches.append("snapshot")
    if first.contract_sha256 != second.contract_sha256:
        mismatches.append("contract")
    if first.terminal is not second.terminal:
        mismatches.append("terminal")
    if first.binary_passed is not second.binary_passed:
        mismatches.append("binary")
    if dict(first.test_outcomes) != dict(second.test_outcomes):
        mismatches.append("tests")
    if dict(first.objective_components) != dict(second.objective_components):
        mismatches.append("components")
    if not _scores_match(first.continuous_score, second.continuous_score):
        mismatches.append("continuous_score")
    return GradingReplayComparison(not mismatches, tuple(mismatches))


def require_executable_outcome(result: GraderResult) -> None:
    """Deny downstream substitution when executable authority is not a hard pass."""
    if result.terminal is not GraderTerminalKind.PASSED or result.binary_passed is not True:
        raise GraderReplayMismatchError()


def classify_candidate_flakiness(
    outcomes: tuple[bool, ...], preregistered_signature: tuple[bool, ...] | None
) -> FlakyTestClassification:
    """Freeze up to one candidate run plus two classification reruns without best-of-N."""
    return FlakyTestClassification(outcomes, preregistered_signature)


def require_intake_stability(
    baseline_outcomes: tuple[bool, ...], gold_patch_outcomes: tuple[bool, ...]
) -> None:
    """Require five fresh baseline and gold-patch passes before task intake."""
    if len(baseline_outcomes) != 5 or len(gold_patch_outcomes) != 5:
        raise GraderContractError("grader_intake_requires_five_fresh_runs")
    if not all(baseline_outcomes):
        raise GraderContractError("grader_baseline_instability")
    if not all(gold_patch_outcomes):
        raise GraderContractError("grader_gold_patch_instability")


async def grade_with_bounded_regrades(
    grader: GraderPort, snapshot: object, contract: GraderContract
) -> tuple[GraderResult, ...]:
    """Regrade only an unavailable frozen input with the same immutable contract."""
    results = [await grader.grade(snapshot, contract)]
    for _ in range(contract.maximum_regrades):
        if results[-1].terminal is not GraderTerminalKind.UNAVAILABLE:
            break
        replay = await grader.grade(snapshot, contract)
        require_matching_replays(results[0], replay)
        results.append(replay)
    return tuple(results)


def require_matching_replays(first: GraderResult, second: GraderResult) -> None:
    """Raise a typed blocker unless repeated frozen grading produces the same outcome."""
    if not compare_grading_replays(first, second).matches:
        raise GraderReplayMismatchError()


def _scores_match(first: float | None, second: float | None) -> bool:
    if first is None or second is None:
        return first is second
    return abs(first - second) <= CONTINUOUS_SCORE_TOLERANCE


@dataclass(frozen=True, slots=True)
class JudgeSlot:
    """One pinned judge role selected by the already-frozen native model lock."""

    slot: int
    model_id: str
    family: str
    reasoning_effort: object
    context_tier: object


@dataclass(frozen=True, slots=True)
class PanelDiversity:
    """Reported scarcity metadata; it never permits a substitute model."""

    label: str
    requires_stronger_calibration: bool
    generator_overlap: bool


@dataclass(frozen=True, slots=True)
class JudgePanelOutcome:
    """The three retained records for one candidate; partial panels remain blocked."""

    candidate_id: str
    view_sha256: str
    protocol_sha256: str
    protocol_artifact: ArtifactRef
    records: tuple[JudgeRecord, ...]
    record_artifacts: tuple[ArtifactRef, ...]

    @property
    def is_complete(self) -> bool:
        return len(self.records) == 3 and all(
            record.status == "completed" for record in self.records
        )

    @property
    def blocking_code(self) -> str | None:
        return None if self.is_complete else "judge_panel_incomplete"


def select_judge_slots(
    model_lock: Mapping[str, object], runtime_lock_sha256: str
) -> tuple[tuple[JudgeSlot, ...], PanelDiversity]:
    """Accept exactly the frozen judge pins and report scarcity without replacement."""

    model_lock_sha256 = model_lock.get("lock_sha256")
    selected = model_lock.get("selected_models")
    if (
        not isinstance(model_lock_sha256, str)
        or not _is_sha256(model_lock_sha256)
        or model_lock.get("runtime_lock_sha256") != runtime_lock_sha256
        or not isinstance(selected, list)
    ):
        raise JudgePanelConformanceError("judge_model_lock_invalid")
    judges = [
        item for item in selected if isinstance(item, Mapping) and item.get("role") == "judge"
    ]
    if len(judges) != 3:
        raise JudgePanelConformanceError("judge_panel_requires_exactly_three_pinned_slots")
    slots: list[JudgeSlot] = []
    for index, item in enumerate(judges, start=1):
        model_id = item.get("native_id")
        family = item.get("family")
        capabilities = item.get("capabilities")
        if (
            not isinstance(model_id, str)
            or not model_id
            or not isinstance(family, str)
            or not family
            or not isinstance(capabilities, Mapping)
            or not all(
                capabilities.get(capability) not in {None, False, "unavailable"}
                for capability in (
                    "tools",
                    "permissions",
                    "context",
                    "events",
                    "cancellation",
                    "sessions",
                )
            )
        ):
            raise JudgePanelConformanceError("judge_model_pin_invalid")
        slots.append(
            JudgeSlot(
                index,
                model_id,
                family,
                item.get("reasoning_effort", "unavailable"),
                item.get("context_tier", "unavailable"),
            )
        )
    if len({slot.model_id for slot in slots}) != 3:
        raise JudgePanelConformanceError("judge_model_ids_not_distinct")
    generator_ids = {
        item.get("native_id")
        for item in selected
        if isinstance(item, Mapping)
        and item.get("role") == "M0"
        and isinstance(item.get("native_id"), str)
    }
    families = {slot.family for slot in slots}
    generator_overlap = bool(generator_ids.intersection(slot.model_id for slot in slots))
    label = (
        "diverse"
        if len(families) == 3 and not generator_overlap
        else ("homogeneous" if len(families) == 1 else "partial")
    )
    return tuple(slots), PanelDiversity(
        label=label,
        requires_stronger_calibration=label != "diverse",
        generator_overlap=generator_overlap,
    )


class JudgePanelRunner:
    """Run at most the single sealed three-judge panel for each blinded candidate."""

    def __init__(
        self,
        store: ArtifactStorePort,
        judge_process: JudgeProcessPort,
        model_lock: Mapping[str, object],
        runtime_lock_sha256: str,
        rubric: FrozenJudgeRubric,
        schedule: FrozenPanelSchedule,
        qualification_protocol: FrozenPanelQualificationProtocol,
        sealed_seed: int,
    ) -> None:
        require_unpaid_conformance_ports(store)
        if not _is_sha256(runtime_lock_sha256):
            raise JudgePanelConformanceError("judge_runtime_lock_invalid")
        self._store = store
        self._judge_process = judge_process
        self._slots, self._diversity = select_judge_slots(model_lock, runtime_lock_sha256)
        self._model_lock_sha256 = _required_hash(
            model_lock, "lock_sha256", "judge_model_lock_invalid"
        )
        self._runtime_lock_sha256 = runtime_lock_sha256
        self._rubric = rubric
        self._schedule = schedule
        qualification_protocol.bind_schedule(schedule)
        self._qualification_protocol = qualification_protocol
        self._order = schedule.order_for_seed(sealed_seed)
        self._protocol_document = {
            "version": "1.0.0",
            "model_lock_sha256": self._model_lock_sha256,
            "runtime_lock_sha256": self._runtime_lock_sha256,
            "rubric_sha256": rubric.sha256,
            "panel_qualification_protocol_sha256": qualification_protocol.sha256,
            "schedule": schedule.document(self._order),
            "judge_models": [
                {"slot": slot.slot, "model_id": slot.model_id, "family": slot.family}
                for slot in self._slots
            ],
            "panel_diversity": {
                "label": self._diversity.label,
                "requires_stronger_calibration": self._diversity.requires_stronger_calibration,
                "generator_overlap": self._diversity.generator_overlap,
            },
        }
        self._protocol_bytes = canonical_bytes(self._protocol_document)
        self._protocol_sha256 = sha256(self._protocol_bytes).hexdigest()
        self._outcomes: dict[str, JudgePanelOutcome] = {}
        self._candidate_bindings: dict[str, tuple[str, str]] = {}
        self._usage = _PanelUsage()
        self._next_schedule_index = 0
        self._lock = asyncio.Lock()

    @property
    def presentation_order(self) -> tuple[str, ...]:
        """The sole sealed order in which previously unjudged candidates may enter."""

        return self._order

    async def judge(
        self, candidate_id: str, view: ArtifactRef, *, evidence_available: bool = True
    ) -> JudgePanelOutcome:
        """Return retained records on replay and never launch a fourth assessment."""

        require_treatment_neutral(candidate_id)
        if candidate_id not in self._schedule.item_ids:
            raise JudgePanelConformanceError("judge_candidate_not_in_sealed_schedule")
        view_sha256 = view.sha256
        binding = (view_sha256, self._protocol_sha256)
        async with self._lock:
            existing = self._outcomes.get(candidate_id)
            if existing is not None:
                if self._candidate_bindings[candidate_id] != binding:
                    raise JudgePanelConformanceError("judge_panel_authorization_conflict")
                return existing
            prior = self._candidate_bindings.get(candidate_id)
            if prior is not None and prior != binding:
                raise JudgePanelConformanceError("judge_panel_authorization_conflict")
            if (
                self._next_schedule_index >= len(self._order)
                or self._order[self._next_schedule_index] != candidate_id
            ):
                raise JudgePanelConformanceError("judge_candidate_order_not_sealed")
            self._candidate_bindings[candidate_id] = binding
            protocol_artifact = self._store.put_bytes(
                self._protocol_bytes,
                media_type="application/json",
                classification="unpaid_conformance",
            )
            if not evidence_available:
                outcome = self._unavailable_outcome(candidate_id, view, protocol_artifact)
            else:
                try:
                    data = self._store.open_verified(view)
                    blinded_view = parse_blinded_view(data)
                except (
                    ArtifactIntegrityError,
                    BlindingConformanceError,
                    JudgePanelConformanceError,
                ):
                    outcome = self._unavailable_outcome(candidate_id, view, protocol_artifact)
                else:
                    outcome = await self._run_panel(
                        candidate_id, view, data, blinded_view, protocol_artifact
                    )
            self._outcomes[candidate_id] = outcome
            self._next_schedule_index += 1
            return outcome

    def _unavailable_outcome(
        self, candidate_id: str, view: ArtifactRef, protocol_artifact: ArtifactRef
    ) -> JudgePanelOutcome:
        records = tuple(
            self._record(candidate_id, view, slot, "unavailable", {}, "judge_evidence_unavailable")
            for slot in self._slots
        )
        return self._stored_outcome(candidate_id, view, protocol_artifact, records)

    async def _run_panel(
        self,
        candidate_id: str,
        view: ArtifactRef,
        view_bytes: bytes,
        blinded_view: Mapping[str, object],
        protocol_artifact: ArtifactRef,
    ) -> JudgePanelOutcome:
        records: list[JudgeRecord] = []
        authorized_artifacts = _authorized_blinded_artifacts(blinded_view)
        for slot in self._slots:
            if not self._usage.can_authorize(self._schedule.limits):
                records.append(
                    self._record(
                        candidate_id, view, slot, "unavailable", {}, "judge_stage_cap_exhausted"
                    )
                )
                continue
            request = JudgeSessionRequest(
                session_id=f"{self._protocol_sha256[:16]}-{candidate_id}-{slot.slot}",
                candidate_id=candidate_id,
                model_id=slot.model_id,
                reasoning_effort=slot.reasoning_effort,
                context_tier=slot.context_tier,
                system_prompt=self._rubric.system_prompt,
                rubric_sha256=self._rubric.sha256,
                tools=self._rubric.tool_schemas,
                decoding_controls=self._rubric.decoding_controls,
                view_bytes=view_bytes,
                wall_seconds_limit=self._schedule.limits.per_session_wall_seconds,
                authorized_blinded_artifacts=authorized_artifacts,
            )
            try:
                result = await self._judge_process.run_judge_session(request)
            except (ConformancePauseError, JudgePanelConformanceError, ProcessLaunchError) as error:
                self._usage = self._usage.plus(
                    JudgeRuntimeResult(
                        "failed",
                        None,
                        failure_code=getattr(error, "code", "judge_process_launch_failed"),
                    )
                )
                records.append(
                    self._record(
                        candidate_id,
                        view,
                        slot,
                        "failed",
                        {},
                        getattr(error, "code", "judge_process_launch_failed"),
                    )
                )
                continue
            if not isinstance(result, JudgeRuntimeResult):
                self._usage = self._usage.plus(
                    JudgeRuntimeResult("failed", None, failure_code="judge_runtime_result_invalid")
                )
                records.append(
                    self._record(
                        candidate_id, view, slot, "failed", {}, "judge_runtime_result_invalid"
                    )
                )
                continue
            self._usage = self._usage.plus(result)
            failure = _judge_limit_failure(result, self._usage, self._schedule.limits)
            if failure is not None:
                records.append(self._record(candidate_id, view, slot, "failed", {}, failure))
            elif result.status != "completed":
                records.append(
                    self._record(
                        candidate_id,
                        view,
                        slot,
                        result.status,
                        {},
                        result.failure_code or "judge_runtime_failed",
                    )
                )
            else:
                try:
                    criteria = parse_judge_response(result.response or {})
                    require_citations_in_view(criteria, blinded_view)
                except JudgePanelConformanceError as error:
                    records.append(self._record(candidate_id, view, slot, "failed", {}, error.code))
                else:
                    records.append(
                        self._record(candidate_id, view, slot, "completed", criteria, None)
                    )
        return self._stored_outcome(candidate_id, view, protocol_artifact, tuple(records))

    def _record(
        self,
        candidate_id: str,
        view: ArtifactRef,
        slot: JudgeSlot,
        status: str,
        criteria: Mapping[str, object],
        failure_code: str | None,
    ) -> JudgeRecord:
        return JudgeRecord(
            candidate_id=candidate_id,
            view_sha256=view.sha256,
            panel_protocol_sha256=self._protocol_sha256,
            schedule_position=self._order.index(candidate_id),
            judge_slot=slot.slot,
            model_id=slot.model_id,
            model_family=slot.family,
            diversity_label=self._diversity.label,
            requires_stronger_calibration=self._diversity.requires_stronger_calibration,
            runtime_lock_sha256=self._runtime_lock_sha256,
            model_lock_sha256=self._model_lock_sha256,
            rubric_sha256=self._rubric.sha256,
            system_prompt_sha256=self._rubric.system_prompt_sha256,
            tools_sha256=self._rubric.tools_sha256,
            decoding_controls_sha256=self._rubric.decoding_controls_sha256,
            status=status,
            criteria=criteria,
            failure_code=failure_code,
        )

    def _stored_outcome(
        self,
        candidate_id: str,
        view: ArtifactRef,
        protocol_artifact: ArtifactRef,
        records: tuple[JudgeRecord, ...],
    ) -> JudgePanelOutcome:
        record_artifacts = tuple(
            self._store.put_bytes(
                record.canonical_bytes,
                media_type="application/json",
                classification="unpaid_conformance",
            )
            for record in records
        )
        return JudgePanelOutcome(
            candidate_id,
            view.sha256,
            self._protocol_sha256,
            protocol_artifact,
            records,
            record_artifacts,
        )


@dataclass(frozen=True, slots=True)
class _PanelUsage:
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

    def plus(self, result: JudgeRuntimeResult) -> _PanelUsage:
        return _PanelUsage(
            self.sessions + 1,
            self.tokens + result.tokens,
            self.tools + result.tool_calls,
            self.active_seconds + result.active_seconds,
            self.wall_seconds + result.wall_seconds,
        )


def _judge_limit_failure(
    result: JudgeRuntimeResult, usage: _PanelUsage, limits: JudgeLimits
) -> str | None:
    if (
        result.tokens > limits.per_session_tokens
        or result.tool_calls > limits.per_session_tools
        or result.active_seconds > limits.per_session_active_seconds
        or result.wall_seconds > limits.per_session_wall_seconds
    ):
        return "judge_session_cap_exceeded"
    if (
        usage.sessions > limits.stage_session_limit
        or usage.tokens > limits.stage_token_limit
        or usage.tools > limits.stage_tool_limit
        or usage.active_seconds > limits.stage_active_seconds_limit
        or usage.wall_seconds > limits.stage_wall_seconds_limit
    ):
        return "judge_stage_cap_exceeded"
    return None


def write_judge_record_manifest(
    store: ArtifactStorePort,
    record: JudgeRecord,
    record_artifact: ArtifactRef,
    protocol_artifact: ArtifactRef,
    view_artifact: ArtifactRef,
    manifest: ArtifactManifest,
) -> ArtifactRef:
    """Write a schema-1.0.0 manifest only for the exact immutable judge record."""

    require_unpaid_conformance_ports(store)
    if (
        manifest.kind != "judge_record"
        or manifest.artifact_id != record_artifact.artifact_id
        or manifest.sha256 != record.sha256
        or manifest.size_bytes != record_artifact.size_bytes
        or set(manifest.source_artifact_ids)
        != {protocol_artifact.artifact_id, view_artifact.artifact_id}
    ):
        raise JudgePanelConformanceError("judge_record_manifest_provenance_mismatch")
    return store.write_manifest(manifest)


def _required_hash(document: Mapping[str, object], key: str, code: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not _is_sha256(value):
        raise JudgePanelConformanceError(code)
    return value


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _authorized_blinded_artifacts(view: Mapping[str, object]) -> Mapping[str, str]:
    """Derive the only tool-readable content from the verified blinded view."""

    evidence = view.get("evidence")
    locations = view.get("artifact_locations")
    if not isinstance(evidence, Mapping) or not isinstance(locations, Mapping):
        raise JudgePanelConformanceError("judge_view_not_blinded")
    artifacts: dict[str, str] = {}
    for name, location in locations.items():
        if not isinstance(name, str) or not isinstance(location, str):
            raise JudgePanelConformanceError("judge_view_not_blinded")
        artifacts[location] = canonical_bytes(
            {"artifact_location": location, "evidence": evidence.get(name)}
        ).decode("utf-8")
    return artifacts
