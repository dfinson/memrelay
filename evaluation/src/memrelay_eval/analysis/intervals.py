"""Simultaneous intervals coupled to a frozen Holm family."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

from memrelay_eval.domain.errors import AnalysisError

from .multiplicity import FrozenClaimFamily, HolmResult


@dataclass(frozen=True, slots=True)
class SimultaneousInterval:
    endpoint_id: str
    point_estimate: float | None
    lower: float | None
    upper: float | None
    confidence_level: float
    procedure: str
    family_sha256: str
    status: str = "estimated"
    sidedness: str = "two-sided"

    def __post_init__(self) -> None:
        if self.status not in {"estimated", "blocked", "missing", "indeterminate"}:
            raise AnalysisError("simultaneous_interval_status_invalid")
        if self.sidedness not in {"two-sided", "lower-one-sided", "upper-one-sided"}:
            raise AnalysisError("simultaneous_interval_sidedness_invalid")
        if not 0.0 < self.confidence_level < 1.0:
            raise AnalysisError("simultaneous_interval_confidence_invalid")
        if self.status != "estimated":
            return
        if self.sidedness == "two-sided":
            values = (self.point_estimate, self.lower, self.upper)
            if any(value is None or not math.isfinite(float(value)) for value in values) or float(
                self.lower
            ) > float(self.upper):
                raise AnalysisError("simultaneous_interval_input_invalid")
        elif self.sidedness == "lower-one-sided":
            if any(
                value is None or not math.isfinite(float(value))
                for value in (self.point_estimate, self.lower)
            ):
                raise AnalysisError("simultaneous_interval_input_invalid")
            if self.upper is not None and (
                not math.isfinite(float(self.upper)) or float(self.lower) > float(self.upper)
            ):
                raise AnalysisError("simultaneous_interval_input_invalid")
        elif (
            self.upper is None
            or self.point_estimate is None
            or not all(math.isfinite(float(value)) for value in (self.point_estimate, self.upper))
        ):
            raise AnalysisError("simultaneous_interval_input_invalid")


def holm_simultaneous_intervals(
    family: FrozenClaimFamily,
    holm: tuple[HolmResult, ...],
    estimates: dict[str, tuple[float, float]],
) -> tuple[SimultaneousInterval, ...]:
    """Emit rank-adjusted normal intervals; no marginal interval is claim-eligible."""

    result_by_id = {result.endpoint_id: result for result in holm}
    if set(result_by_id) != set(family.endpoint_ids) or set(estimates) != set(family.endpoint_ids):
        raise AnalysisError("simultaneous_interval_membership_changed")
    intervals: list[SimultaneousInterval] = []
    for endpoint_id in family.endpoint_ids:
        result = result_by_id[endpoint_id]
        if result.family_sha256 != family.family_sha256:
            raise AnalysisError("simultaneous_interval_family_drift")
        if result.status != "estimated":
            intervals.append(
                SimultaneousInterval(
                    endpoint_id,
                    None,
                    None,
                    None,
                    0.95,
                    "holm-compatible",
                    family.family_sha256,
                    "blocked",
                )
            )
            continue
        point, standard_error = estimates[endpoint_id]
        if (
            not all(math.isfinite(value) for value in (point, standard_error))
            or standard_error <= 0
        ):
            raise AnalysisError("simultaneous_interval_input_invalid")
        if family.endpoint_direction(endpoint_id) in {"non_inferiority", "harm"}:
            critical = NormalDist().inv_cdf(0.975)
            intervals.append(
                SimultaneousInterval(
                    endpoint_id,
                    point,
                    point - critical * standard_error,
                    None,
                    0.975,
                    "holm-compatible",
                    family.family_sha256,
                    sidedness="lower-one-sided",
                )
            )
            continue
        adjusted_alpha = family.alpha / (len(family.endpoint_ids) - int(result.rank) + 1)
        critical = NormalDist().inv_cdf(1.0 - adjusted_alpha / 2.0)
        intervals.append(
            SimultaneousInterval(
                endpoint_id,
                point,
                point - critical * standard_error,
                point + critical * standard_error,
                1.0 - adjusted_alpha,
                "holm-compatible",
                family.family_sha256,
                sidedness="two-sided",
            )
        )
    return tuple(intervals)


def require_claim_eligible_interval(
    interval: SimultaneousInterval, family: FrozenClaimFamily
) -> None:
    if (
        interval.status != "estimated"
        or interval.procedure != "holm-compatible"
        or interval.family_sha256 != family.family_sha256
    ):
        raise AnalysisError("marginal_or_drifted_interval_forbidden")
