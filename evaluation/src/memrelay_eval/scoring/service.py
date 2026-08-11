"""Deterministic executable-grading policy without assignment access."""

from __future__ import annotations

from dataclasses import dataclass

from memrelay_eval.domain.entities import (
    FlakyTestClassification,
    GraderContract,
    GraderResult,
)
from memrelay_eval.domain.errors import GraderContractError, GraderReplayMismatchError
from memrelay_eval.domain.ports import GraderPort
from memrelay_eval.domain.states import GraderTerminalKind

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
