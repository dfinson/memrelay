"""Frozen Holm-family registration and deterministic familywise inference."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import AnalysisError

HOLM_METHOD = "holm"
HOLM_VERSION = "1.0.0"
FAMILYWISE_ALPHA = 0.05
_FAMILY_HASH_CACHE: dict[FrozenClaimFamily, str] = {}

_MODE_ENDPOINT_POLICIES = {
    "reliability": (
        ("EP-PRIM-SUCCESS", "benefit", "difference", 0.05),
        ("EP-QUAL", "benefit", "difference", 0.05),
        ("EP-HARM", "harm", "difference", -0.02),
    ),
    "efficiency": (
        ("EP-SUCC-NI", "non_inferiority", "difference", -0.02),
        ("EP-QUAL", "benefit", "difference", 0.05),
        ("EP-COST", "superiority", "ratio", 0.90),
        ("EP-WALL", "superiority", "ratio", 0.90),
        ("EP-HARM", "harm", "difference", -0.02),
    ),
    "dual": (
        ("EP-PRIM-SUCCESS", "benefit", "difference", 0.05),
        ("EP-QUAL", "benefit", "difference", 0.05),
        ("EP-COST", "superiority", "ratio", 0.90),
        ("EP-WALL", "superiority", "ratio", 0.90),
        ("EP-HARM", "harm", "difference", -0.02),
    ),
}


def _sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value.isascii()
        and all(c in "0123456789abcdef" for c in value)
    )


@dataclass(frozen=True, slots=True)
class FrozenClaimFamily:
    """An outcome-independent, complete confirmatory family."""

    family_id: str
    mode: str
    protocol_sha256: str
    assignment_plan_sha256: str
    source_dataset_manifest_sha256: str
    estimand_registry_sha256: str
    environment_fingerprint_sha256: str
    model_role: str
    endpoint_ids: tuple[str, ...]
    endpoint_directions: tuple[str, ...]
    endpoint_scales: tuple[str, ...]
    endpoint_margins: tuple[float, ...]
    categorical_gate_ids: tuple[str, ...]
    efficiency_selection: str | None = None
    enrolled_n: int = 512
    alpha: float = FAMILYWISE_ALPHA
    method: str = HOLM_METHOD
    method_version: str = HOLM_VERSION
    frozen_before_outcomes: bool = True
    sealed_claim_protocol_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.family_id or self.mode not in _MODE_ENDPOINT_POLICIES:
            raise AnalysisError("claim_family_invalid")
        if not all(
            _sha256(value)
            for value in (
                self.protocol_sha256,
                self.assignment_plan_sha256,
                self.source_dataset_manifest_sha256,
                self.estimand_registry_sha256,
                self.environment_fingerprint_sha256,
            )
        ):
            raise AnalysisError("claim_family_lineage_invalid")
        if not self.model_role:
            raise AnalysisError("claim_family_model_role_invalid")
        expected = _MODE_ENDPOINT_POLICIES[self.mode]
        if tuple(self.endpoint_ids) != tuple(item[0] for item in expected):
            raise AnalysisError("claim_family_cardinality_or_order_invalid")
        if (
            len(self.endpoint_directions) != len(expected)
            or len(self.endpoint_scales) != len(expected)
            or len(self.endpoint_margins) != len(expected)
        ):
            raise AnalysisError("claim_family_endpoint_policy_incomplete")
        directions = tuple(self.endpoint_directions)
        scales = tuple(self.endpoint_scales)
        margins = tuple(float(margin) for margin in self.endpoint_margins)
        if directions != tuple(item[1] for item in expected):
            raise AnalysisError("claim_family_direction_invalid")
        if scales != tuple(item[2] for item in expected):
            raise AnalysisError("claim_family_scale_invalid")
        if any(not math.isfinite(margin) for margin in margins) or margins != tuple(
            item[3] for item in expected
        ):
            raise AnalysisError("claim_family_margin_invalid")
        if not self.categorical_gate_ids or len(set(self.categorical_gate_ids)) != len(
            self.categorical_gate_ids
        ):
            raise AnalysisError("claim_family_gates_invalid")
        if self.enrolled_n != 512 or self.alpha != FAMILYWISE_ALPHA:
            raise AnalysisError("claim_family_fixed_information_invalid")
        if self.method != HOLM_METHOD or self.method_version != HOLM_VERSION:
            raise AnalysisError("claim_family_method_unsupported")
        if not self.frozen_before_outcomes:
            raise AnalysisError("claim_family_outcome_access_forbidden")
        if not _sha256(self.sealed_claim_protocol_sha256):
            raise AnalysisError("claim_family_preregistration_invalid")
        if self.mode == "reliability":
            if self.efficiency_selection is not None:
                raise AnalysisError("claim_family_efficiency_selection_invalid")
        elif self.mode == "efficiency":
            if self.efficiency_selection not in {"cost", "wall", "intersection"}:
                raise AnalysisError("claim_family_efficiency_selection_invalid")
        elif self.efficiency_selection != "intersection":
            raise AnalysisError("claim_family_dual_efficiency_selection_required")
        object.__setattr__(self, "endpoint_ids", tuple(self.endpoint_ids))
        object.__setattr__(self, "endpoint_directions", tuple(self.endpoint_directions))
        object.__setattr__(self, "endpoint_scales", tuple(self.endpoint_scales))
        object.__setattr__(self, "endpoint_margins", margins)
        object.__setattr__(self, "categorical_gate_ids", tuple(self.categorical_gate_ids))

    @property
    def family_registration_sha256(self) -> str:
        return canonical_digest(self._registration_document())

    @property
    def family_sha256(self) -> str:
        try:
            return _FAMILY_HASH_CACHE[self]
        except KeyError:
            digest = canonical_digest(self.to_document())
            _FAMILY_HASH_CACHE[self] = digest
            return digest

    def to_document(self) -> dict[str, object]:
        return {
            **self._registration_document(),
            "sealed_claim_protocol_sha256": self.sealed_claim_protocol_sha256,
        }

    def _registration_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "frozen_claim_family",
            "family_id": self.family_id,
            "mode": self.mode,
            "protocol_sha256": self.protocol_sha256,
            "assignment_plan_sha256": self.assignment_plan_sha256,
            "source_dataset_manifest_sha256": self.source_dataset_manifest_sha256,
            "estimand_registry_sha256": self.estimand_registry_sha256,
            "environment_fingerprint_sha256": self.environment_fingerprint_sha256,
            "model_role": self.model_role,
            "endpoint_ids": list(self.endpoint_ids),
            "endpoint_directions": list(self.endpoint_directions),
            "endpoint_scales": list(self.endpoint_scales),
            "endpoint_margins": list(self.endpoint_margins),
            "categorical_gate_ids": list(self.categorical_gate_ids),
            "efficiency_selection": self.efficiency_selection,
            "enrolled_n": self.enrolled_n,
            "alpha": self.alpha,
            "method": self.method,
            "method_version": self.method_version,
            "frozen_before_outcomes": self.frozen_before_outcomes,
        }

    def endpoint_scale(self, endpoint_id: str) -> str:
        try:
            return self.endpoint_scales[self.endpoint_ids.index(endpoint_id)]
        except ValueError as error:
            raise AnalysisError("claim_family_endpoint_membership_changed") from error

    def endpoint_direction(self, endpoint_id: str) -> str:
        try:
            return self.endpoint_directions[self.endpoint_ids.index(endpoint_id)]
        except ValueError as error:
            raise AnalysisError("claim_family_endpoint_membership_changed") from error

    @property
    def selected_efficiency_endpoint_ids(self) -> tuple[str, ...]:
        if self.mode == "reliability":
            return ()
        if self.efficiency_selection == "cost":
            return ("EP-COST",)
        if self.efficiency_selection == "wall":
            return ("EP-WALL",)
        return ("EP-COST", "EP-WALL")


@dataclass(frozen=True, slots=True)
class EndpointPValue:
    endpoint_id: str
    raw_p_value: float | None
    status: str = "estimated"

    def __post_init__(self) -> None:
        if self.status not in {"estimated", "blocked", "missing", "indeterminate"}:
            raise AnalysisError("endpoint_inference_status_invalid")
        if self.status == "estimated":
            if self.raw_p_value is None or not 0.0 <= self.raw_p_value <= 1.0:
                raise AnalysisError("endpoint_p_value_invalid")
        elif self.raw_p_value is not None:
            raise AnalysisError("blocked_endpoint_p_value_forbidden")


@dataclass(frozen=True, slots=True)
class HolmResult:
    endpoint_id: str
    raw_p_value: float | None
    adjusted_p_value: float | None
    rank: int | None
    rejection: bool
    status: str
    family_sha256: str
    gate_trace: tuple[str, ...]


def holm_fwer(
    family: FrozenClaimFamily, values: Sequence[EndpointPValue]
) -> tuple[HolmResult, ...]:
    """Apply the sole permitted v1 multiplicity procedure, failing closed on gaps."""

    by_id = {item.endpoint_id: item for item in values}
    if len(by_id) != len(values) or set(by_id) != set(family.endpoint_ids):
        raise AnalysisError("claim_family_endpoint_membership_changed")
    blocked = [item.endpoint_id for item in values if item.status != "estimated"]
    family_hash = family.family_sha256
    if blocked:
        return tuple(
            HolmResult(
                endpoint_id=endpoint_id,
                raw_p_value=by_id[endpoint_id].raw_p_value,
                adjusted_p_value=None,
                rank=None,
                rejection=False,
                status="blocked" if endpoint_id in blocked else "blocked",
                family_sha256=family_hash,
                gate_trace=("family_incomplete", *tuple(sorted(blocked))),
            )
            for endpoint_id in family.endpoint_ids
        )
    ordered = sorted(
        values,
        key=lambda item: (float(item.raw_p_value), family.endpoint_ids.index(item.endpoint_id)),
    )
    adjusted: dict[str, tuple[float, int]] = {}
    running = 0.0
    size = len(ordered)
    for rank, item in enumerate(ordered, start=1):
        value = min(1.0, (size - rank + 1) * float(item.raw_p_value))
        running = max(running, value)
        adjusted[item.endpoint_id] = (running, rank)
    return tuple(
        HolmResult(
            endpoint_id=endpoint_id,
            raw_p_value=by_id[endpoint_id].raw_p_value,
            adjusted_p_value=adjusted[endpoint_id][0],
            rank=adjusted[endpoint_id][1],
            rejection=adjusted[endpoint_id][0] <= family.alpha,
            status="estimated",
            family_sha256=family_hash,
            gate_trace=(HOLM_METHOD, HOLM_VERSION, f"rank:{adjusted[endpoint_id][1]}"),
        )
        for endpoint_id in family.endpoint_ids
    )
