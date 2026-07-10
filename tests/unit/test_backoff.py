"""Unit tests for the ingester's exponential-backoff delay math (E3-S5 #32).

Pure and deterministic: injecting the ``rng`` pins the jitter, so the whole schedule
is a function of ``attempt`` and asserts exactly — no sleeping, no randomness.
"""

from __future__ import annotations

from memrelay.ingest.backoff import (
    DEFAULT_BASE_DELAY,
    DEFAULT_MAX_DELAY,
    next_delay,
)


def _ceiling_rng() -> float:
    """Full jitter that always returns the ceiling (rng == 1.0)."""
    return 1.0


def _zero_rng() -> float:
    return 0.0


def test_delay_doubles_each_attempt_at_full_jitter() -> None:
    # rng==1.0 collapses full jitter to the raw exponential ceiling: base * 2**attempt.
    delays = [next_delay(a, base=0.5, cap=1000.0, rng=_ceiling_rng) for a in range(4)]
    assert delays == [0.5, 1.0, 2.0, 4.0]


def test_delay_is_capped() -> None:
    # The exponential would be 0.5 * 2**10 = 512, but the cap clamps it.
    assert next_delay(10, base=0.5, cap=30.0, rng=_ceiling_rng) == 30.0


def test_full_jitter_scales_within_zero_and_ceiling() -> None:
    ceiling = 4.0  # base=0.5, attempt=3 -> 0.5*8
    assert next_delay(3, base=0.5, cap=1000.0, rng=_zero_rng) == 0.0
    assert next_delay(3, base=0.5, cap=1000.0, rng=lambda: 0.5) == ceiling * 0.5
    assert next_delay(3, base=0.5, cap=1000.0, rng=_ceiling_rng) == ceiling


def test_jitter_stays_within_bounds_across_attempts() -> None:
    # For any rng in [0, 1) the delay must land in [0, ceiling] for every attempt.
    rng_values = iter([0.0, 0.9999, 0.5, 0.1, 0.7])

    def rng() -> float:
        return next(rng_values)

    for attempt in range(5):
        ceiling = min(DEFAULT_MAX_DELAY, DEFAULT_BASE_DELAY * (2.0**attempt))
        delay = next_delay(attempt, base=DEFAULT_BASE_DELAY, cap=DEFAULT_MAX_DELAY, rng=rng)
        assert 0.0 <= delay <= ceiling


def test_negative_attempt_is_clamped_to_zero() -> None:
    assert next_delay(-5, base=0.5, cap=30.0, rng=_ceiling_rng) == 0.5


def test_unbounded_attempt_does_not_overflow_and_returns_cap() -> None:
    # retry-forever (max_retries=None) drives attempt arbitrarily high; must stay capped.
    assert next_delay(10_000, base=0.5, cap=30.0, rng=_ceiling_rng) == 30.0


def test_defaults_are_sane() -> None:
    assert DEFAULT_BASE_DELAY > 0
    assert DEFAULT_MAX_DELAY >= DEFAULT_BASE_DELAY
    # First attempt at full jitter is exactly the base delay.
    assert next_delay(0, rng=_ceiling_rng) == DEFAULT_BASE_DELAY
