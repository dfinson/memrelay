"""Frozen blinded-judge protocol, response contract, and record serialization."""

from __future__ import annotations

import json
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.errors import JudgePanelConformanceError
from memrelay_eval.scoring.blinding import BlindingPolicy, detect_direct_leaks

JUDGE_PROTOCOL_VERSION = "1.0.0"
JUDGE_RECORD_SCHEMA_VERSION = "1.0.0"
NATIVE_DECODING_CONTROLS = MappingProxyType({"session_defaults": "github-copilot-sdk-1.0.8"})
JUDGE_CRITERIA = (
    "uncovered_requirement_satisfaction",
    "semantic_appropriateness",
    "maintainability",
    "unnecessary_complexity",
    "repository_fit",
    "evidence_supported_confidence",
)
_ARTIFACT_LOCATION = re.compile(r"^artifact://blinded/[a-f0-9]{64}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_TERMINAL_STATUSES = frozenset({"completed", "failed", "unavailable"})
_SESSION_RESPONSE_CONTRACTS = frozenset({"judge", "adjudication"})


@dataclass(frozen=True, slots=True)
class JudgeLimits:
    """The complete, finite judge envelope pinned before a stage can call a provider."""

    per_session_tokens: int
    per_session_tools: int
    per_session_active_seconds: float
    per_session_wall_seconds: float
    stage_session_limit: int
    stage_token_limit: int
    stage_tool_limit: int
    stage_active_seconds_limit: float
    stage_wall_seconds_limit: float
    concurrency_limit: int = 1

    def __post_init__(self) -> None:
        values = (
            self.per_session_tokens,
            self.per_session_tools,
            self.per_session_active_seconds,
            self.per_session_wall_seconds,
            self.stage_session_limit,
            self.stage_token_limit,
            self.stage_tool_limit,
            self.stage_active_seconds_limit,
            self.stage_wall_seconds_limit,
            self.concurrency_limit,
        )
        if any(not isinstance(value, (int, float)) or value <= 0 for value in values):
            raise JudgePanelConformanceError("judge_limits_invalid")
        if self.concurrency_limit > 3:
            raise JudgePanelConformanceError("judge_concurrency_exceeds_panel")

    def document(self) -> dict[str, int | float]:
        return {
            "per_session_tokens": self.per_session_tokens,
            "per_session_tools": self.per_session_tools,
            "per_session_active_seconds": self.per_session_active_seconds,
            "per_session_wall_seconds": self.per_session_wall_seconds,
            "stage_session_limit": self.stage_session_limit,
            "stage_token_limit": self.stage_token_limit,
            "stage_tool_limit": self.stage_tool_limit,
            "stage_active_seconds_limit": self.stage_active_seconds_limit,
            "stage_wall_seconds_limit": self.stage_wall_seconds_limit,
            "concurrency_limit": self.concurrency_limit,
        }


@dataclass(frozen=True, slots=True)
class FrozenJudgeRubric:
    """Versioned rubric and read-only tools shared identically by every judge."""

    system_prompt: str = (
        "You are a blinded quality judge. Evaluate only the supplied blinded evidence. "
        "Return one JSON object that exactly follows the required structured result. "
        "Do not infer treatment, provider, task-agent identity, cost, or other judges."
    )
    tool_schemas: tuple[Mapping[str, object], ...] = field(
        default_factory=lambda: (
            MappingProxyType(
                {
                    "name": "read_blinded_artifact",
                    "description": "Read a supplied artifact://blinded location.",
                    "input_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["location"],
                        "properties": {
                            "location": {
                                "type": "string",
                                "pattern": "^artifact://blinded/[a-f0-9]{64}$",
                            }
                        },
                    },
                    "read_only": True,
                }
            ),
        )
    )
    decoding_controls: Mapping[str, object] = field(
        default_factory=lambda: NATIVE_DECODING_CONTROLS
    )
    version: str = JUDGE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.version != JUDGE_PROTOCOL_VERSION or not self.system_prompt:
            raise JudgePanelConformanceError("judge_rubric_version_invalid")
        tools = tuple(dict(tool) for tool in self.tool_schemas)
        if not tools or any(tool.get("read_only") is not True for tool in tools):
            raise JudgePanelConformanceError("judge_tools_must_be_read_only")
        if dict(self.decoding_controls) != dict(NATIVE_DECODING_CONTROLS):
            raise JudgePanelConformanceError("judge_decoding_controls_invalid")
        object.__setattr__(self, "tool_schemas", tuple(MappingProxyType(tool) for tool in tools))
        object.__setattr__(
            self, "decoding_controls", MappingProxyType(dict(self.decoding_controls))
        )

    def document(self) -> dict[str, object]:
        return {
            "version": self.version,
            "system_prompt": self.system_prompt,
            "criteria": list(JUDGE_CRITERIA),
            "tool_schemas": [dict(tool) for tool in self.tool_schemas],
            "decoding_controls": dict(self.decoding_controls),
            "response_schema": "judge-record/1.0.0",
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
class FrozenPanelSchedule:
    """A pre-outcome schedule that commits candidate, duplicate, and sentinel order."""

    primary_candidate_ids: tuple[str, ...]
    human_calibration_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    sentinel_ids: tuple[str, ...]
    sealed_seed_commitment: str
    limits: JudgeLimits
    version: str = JUDGE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        all_ids = (
            self.primary_candidate_ids
            + self.human_calibration_ids
            + self.duplicate_ids
            + self.sentinel_ids
        )
        if (
            self.version != JUDGE_PROTOCOL_VERSION
            or not self.primary_candidate_ids
            or not self.human_calibration_ids
            or not self.duplicate_ids
            or not self.sentinel_ids
            or any(not isinstance(item, str) or not item for item in all_ids)
            or len(set(all_ids)) != len(all_ids)
            or not _SHA256.fullmatch(self.sealed_seed_commitment)
        ):
            raise JudgePanelConformanceError("judge_schedule_invalid")
        if self.limits.stage_session_limit != len(all_ids) * 3:
            raise JudgePanelConformanceError("judge_schedule_session_envelope_invalid")
        object.__setattr__(self, "primary_candidate_ids", tuple(self.primary_candidate_ids))
        object.__setattr__(self, "human_calibration_ids", tuple(self.human_calibration_ids))
        object.__setattr__(self, "duplicate_ids", tuple(self.duplicate_ids))
        object.__setattr__(self, "sentinel_ids", tuple(self.sentinel_ids))

    @property
    def item_ids(self) -> tuple[str, ...]:
        return (
            self.primary_candidate_ids
            + self.human_calibration_ids
            + self.duplicate_ids
            + self.sentinel_ids
        )

    def order_for_seed(self, seed: int) -> tuple[str, ...]:
        """Reveal a deterministic presentation order only for its committed seed."""

        if sha256(str(seed).encode("ascii")).hexdigest() != self.sealed_seed_commitment:
            raise JudgePanelConformanceError("judge_schedule_seed_commitment_mismatch")
        order = list(self.item_ids)
        random.Random(seed).shuffle(order)
        return tuple(order)

    def document(self, order: Sequence[str]) -> dict[str, object]:
        if tuple(order) != tuple(item for item in order if item in set(self.item_ids)) or set(
            order
        ) != set(self.item_ids):
            raise JudgePanelConformanceError("judge_schedule_order_invalid")
        return {
            "version": self.version,
            "primary_candidate_ids": list(self.primary_candidate_ids),
            "human_calibration_ids": list(self.human_calibration_ids),
            "duplicate_ids": list(self.duplicate_ids),
            "sentinel_ids": list(self.sentinel_ids),
            "sealed_seed_commitment": self.sealed_seed_commitment,
            "presentation_order": list(order),
            "limits": self.limits.document(),
        }


@dataclass(frozen=True, slots=True)
class JudgeCriterionScore:
    """One normalized score with uncertainty and blinded evidence citations."""

    score: float
    uncertainty: float
    citations: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.score, bool)
            or isinstance(self.uncertainty, bool)
            or not isinstance(self.score, (int, float))
            or not isinstance(self.uncertainty, (int, float))
            or not 0 <= self.score <= 1
            or not 0 <= self.uncertainty <= 1
            or not self.citations
            or any(
                not isinstance(citation, str) or not _ARTIFACT_LOCATION.fullmatch(citation)
                for citation in self.citations
            )
        ):
            raise JudgePanelConformanceError("judge_criterion_invalid")
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "uncertainty", float(self.uncertainty))
        object.__setattr__(self, "citations", tuple(self.citations))

    def document(self) -> dict[str, object]:
        return {
            "score": self.score,
            "uncertainty": self.uncertainty,
            "citations": list(self.citations),
        }


@dataclass(frozen=True, slots=True)
class JudgeRuntimeResult:
    """Value-minimized terminal result returned by one fresh runtime session."""

    status: str
    response: Mapping[str, object] | None
    tokens: int = 0
    tool_calls: int = 0
    active_seconds: float = 0.0
    wall_seconds: float = 0.0
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _TERMINAL_STATUSES:
            raise JudgePanelConformanceError("judge_runtime_status_invalid")
        if self.status == "completed" and self.response is None:
            raise JudgePanelConformanceError("judge_runtime_completed_response_missing")
        if self.status != "completed" and not self.failure_code:
            raise JudgePanelConformanceError("judge_runtime_failure_code_missing")
        values = (self.tokens, self.tool_calls, self.active_seconds, self.wall_seconds)
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            for value in values
        ):
            raise JudgePanelConformanceError("judge_runtime_usage_invalid")
        object.__setattr__(
            self,
            "response",
            MappingProxyType(dict(self.response)) if self.response is not None else None,
        )


@dataclass(frozen=True, slots=True)
class JudgeSessionRequest:
    """The sole evidence package permitted to enter one judge SDK session."""

    session_id: str
    candidate_id: str
    model_id: str
    reasoning_effort: object
    context_tier: object
    system_prompt: str
    rubric_sha256: str
    tools: tuple[Mapping[str, object], ...]
    decoding_controls: Mapping[str, object]
    view_bytes: bytes
    wall_seconds_limit: float
    authorized_blinded_artifacts: Mapping[str, str] = field(default_factory=dict)
    response_contract: str = "judge"
    disputed_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.session_id
            or not self.candidate_id
            or not self.model_id
            or not _SHA256.fullmatch(self.rubric_sha256)
            or not self.system_prompt
            or not self.view_bytes
            or self.wall_seconds_limit <= 0
            or self.response_contract not in _SESSION_RESPONSE_CONTRACTS
        ):
            raise JudgePanelConformanceError("judge_session_request_invalid")
        if self.response_contract == "judge" and self.disputed_criteria:
            raise JudgePanelConformanceError("judge_session_request_invalid")
        if self.response_contract == "adjudication" and (
            not self.disputed_criteria
            or len(set(self.disputed_criteria)) != len(self.disputed_criteria)
            or any(name not in JUDGE_CRITERIA for name in self.disputed_criteria)
        ):
            raise JudgePanelConformanceError("judge_session_request_invalid")
        if (
            len(self.tools) != 1
            or self.tools[0].get("name") != "read_blinded_artifact"
            or self.tools[0].get("read_only") is not True
        ):
            raise JudgePanelConformanceError("judge_session_tools_not_read_only")
        if any(
            not isinstance(location, str)
            or not _ARTIFACT_LOCATION.fullmatch(location)
            or not isinstance(content, str)
            for location, content in self.authorized_blinded_artifacts.items()
        ):
            raise JudgePanelConformanceError("judge_session_artifacts_invalid")
        object.__setattr__(
            self, "tools", tuple(MappingProxyType(dict(tool)) for tool in self.tools)
        )
        object.__setattr__(
            self, "decoding_controls", MappingProxyType(dict(self.decoding_controls))
        )
        object.__setattr__(
            self,
            "authorized_blinded_artifacts",
            MappingProxyType(dict(self.authorized_blinded_artifacts)),
        )
        object.__setattr__(self, "disputed_criteria", tuple(self.disputed_criteria))

    @property
    def prompt(self) -> str:
        if self.response_contract == "adjudication":
            criteria = ", ".join(self.disputed_criteria)
            return (
                f"{self.system_prompt}\n"
                f"Rubric SHA-256: {self.rubric_sha256}\n"
                "Return JSON with a resolutions object containing exactly these disputed "
                f"criteria: {criteria}. Each resolution must have normalized score, resolution, "
                "uncertainty, and blinded artifact citations. Do not return any other criterion "
                "or infer identity, treatment, provider, credentials, cost, or other evidence.\n"
                f"Blinded adjudication evidence:\n{self.view_bytes.decode('utf-8')}"
            )
        return (
            f"{self.system_prompt}\n"
            f"Rubric SHA-256: {self.rubric_sha256}\n"
            "Return JSON with a criteria object containing exactly the frozen criterion names. "
            "Each criterion must have normalized score, uncertainty, and blinded artifact "
            "citations.\n"
            f"Blinded evidence:\n{self.view_bytes.decode('utf-8')}"
        )


@dataclass(frozen=True, slots=True)
class JudgeRecord:
    """Immutable individual panel record; a non-complete record remains terminal evidence."""

    candidate_id: str
    view_sha256: str
    panel_protocol_sha256: str
    schedule_position: int
    judge_slot: int
    model_id: str
    model_family: str
    diversity_label: str
    requires_stronger_calibration: bool
    runtime_lock_sha256: str
    model_lock_sha256: str
    rubric_sha256: str
    system_prompt_sha256: str
    tools_sha256: str
    decoding_controls_sha256: str
    status: str
    criteria: Mapping[str, JudgeCriterionScore] = field(default_factory=dict)
    failure_code: str | None = None
    schema_version: str = JUDGE_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        hashes = (
            self.view_sha256,
            self.panel_protocol_sha256,
            self.runtime_lock_sha256,
            self.model_lock_sha256,
            self.rubric_sha256,
            self.system_prompt_sha256,
            self.tools_sha256,
            self.decoding_controls_sha256,
        )
        if (
            self.schema_version != JUDGE_RECORD_SCHEMA_VERSION
            or self.status not in _TERMINAL_STATUSES
            or self.judge_slot not in {1, 2, 3}
            or self.schedule_position < 0
            or self.diversity_label not in {"diverse", "partial", "homogeneous"}
            or not self.candidate_id
            or not self.model_id
            or not self.model_family
            or any(not _SHA256.fullmatch(value) for value in hashes)
        ):
            raise JudgePanelConformanceError("judge_record_invalid")
        criteria = dict(self.criteria)
        if self.status == "completed":
            if set(criteria) != set(JUDGE_CRITERIA) or self.failure_code is not None:
                raise JudgePanelConformanceError("judge_record_completed_shape_invalid")
        elif criteria or not self.failure_code:
            raise JudgePanelConformanceError("judge_record_failure_shape_invalid")
        object.__setattr__(self, "criteria", MappingProxyType(criteria))

    def document(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "view_sha256": self.view_sha256,
            "panel_protocol_sha256": self.panel_protocol_sha256,
            "schedule_position": self.schedule_position,
            "judge_slot": self.judge_slot,
            "model": {"id": self.model_id, "family": self.model_family},
            "panel_diversity": {
                "label": self.diversity_label,
                "requires_stronger_calibration": self.requires_stronger_calibration,
            },
            "pins": {
                "runtime_lock_sha256": self.runtime_lock_sha256,
                "model_lock_sha256": self.model_lock_sha256,
                "rubric_sha256": self.rubric_sha256,
                "system_prompt_sha256": self.system_prompt_sha256,
                "tools_sha256": self.tools_sha256,
                "decoding_controls_sha256": self.decoding_controls_sha256,
            },
            "status": self.status,
        }
        if self.status == "completed":
            result["criteria"] = {
                criterion: self.criteria[criterion].document() for criterion in JUDGE_CRITERIA
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


def parse_judge_response(response: Mapping[str, object]) -> Mapping[str, JudgeCriterionScore]:
    """Accept exactly the frozen structured response; malformed output is never guessed."""

    criteria = response.get("criteria")
    if (
        set(response) != {"criteria"}
        or not isinstance(criteria, Mapping)
        or set(criteria) != set(JUDGE_CRITERIA)
    ):
        raise JudgePanelConformanceError("judge_response_schema_invalid")
    parsed: dict[str, JudgeCriterionScore] = {}
    for name in JUDGE_CRITERIA:
        value = criteria[name]
        if not isinstance(value, Mapping) or set(value) != {"score", "uncertainty", "citations"}:
            raise JudgePanelConformanceError("judge_response_criterion_schema_invalid")
        citations = value["citations"]
        if not isinstance(citations, Sequence) or isinstance(citations, (str, bytes)):
            raise JudgePanelConformanceError("judge_response_citations_invalid")
        parsed[name] = JudgeCriterionScore(value["score"], value["uncertainty"], tuple(citations))
    return MappingProxyType(parsed)


def require_citations_in_view(
    criteria: Mapping[str, JudgeCriterionScore], view: Mapping[str, object]
) -> None:
    """Require every returned citation to name a supplied blinded artifact location."""

    locations = view.get("artifact_locations")
    if not isinstance(locations, Mapping):
        raise JudgePanelConformanceError("judge_view_not_blinded")
    allowed = {value for value in locations.values() if isinstance(value, str)}
    if any(
        citation not in allowed
        for criterion in criteria.values()
        for citation in criterion.citations
    ):
        raise JudgePanelConformanceError("judge_response_citation_not_in_view")


def parse_blinded_view(data: bytes) -> Mapping[str, object]:
    """Reject an unblinded or malformed input before any judge runtime is called."""

    try:
        view = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JudgePanelConformanceError("judge_view_invalid_json") from error
    if (
        not isinstance(view, Mapping)
        or set(view)
        != {
            "schema_version",
            "source",
            "policy_sha256",
            "transform_sha256",
            "evidence",
            "artifact_locations",
        }
        or view.get("schema_version") != "1.0.0"
        or not isinstance(view.get("source"), Mapping)
        or set(view["source"]) != {"artifact_id", "sha256"}
        or not isinstance(view["source"].get("artifact_id"), str)
        or not re.fullmatch(r"art_[a-f0-9]{32}", view["source"]["artifact_id"])
        or not isinstance(view["source"].get("sha256"), str)
        or not _SHA256.fullmatch(view["source"]["sha256"])
        or not isinstance(view.get("policy_sha256"), str)
        or not _SHA256.fullmatch(view["policy_sha256"])
        or not isinstance(view.get("transform_sha256"), str)
        or not _SHA256.fullmatch(view["transform_sha256"])
        or not isinstance(view.get("evidence"), Mapping)
        or not isinstance(view.get("artifact_locations"), Mapping)
        or any(
            not isinstance(location, str) or not _ARTIFACT_LOCATION.fullmatch(location)
            for location in view["artifact_locations"].values()
        )
    ):
        raise JudgePanelConformanceError("judge_view_not_blinded")
    if detect_direct_leaks(view, BlindingPolicy()):
        raise JudgePanelConformanceError("judge_view_direct_leak")
    return MappingProxyType(dict(view))
