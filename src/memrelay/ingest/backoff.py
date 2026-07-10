"""Exponential-backoff delay math for the ingester's retry loop (E3-S5 #32).

The ingester retries a failed ``engine.note`` when the LLM/engine is *transiently*
unavailable (see :mod:`memrelay.ingest.ingester`). The wait between attempts uses
**exponential backoff with full jitter** — the classic AWS-style policy — so a fleet
of retriers never re-synchronises into a thundering herd against a recovering engine:

    delay(attempt) = random() * min(cap, base * 2**attempt)

This module is deliberately pure and stdlib-only (no asyncio, no engine, no spool) so
the delay schedule is trivially unit-testable: inject a deterministic ``rng`` and the
output is a pure function of ``attempt``. The *waiting* itself (interruptible on the
daemon's stop event) lives in the ingester; this module only decides *how long*.
"""

from __future__ import annotations

import random
from collections.abc import Callable

#: Base delay (seconds) for the first retry — the ``base`` in ``base * 2**attempt``.
DEFAULT_BASE_DELAY = 0.5
#: Upper bound (seconds) on any single backoff wait, however many attempts have failed.
DEFAULT_MAX_DELAY = 30.0
#: Clamp on the exponent so ``2**attempt`` can never overflow when retries are
#: unbounded (``max_retries=None``); the ceiling is capped long before this matters.
_MAX_EXPONENT = 32


def next_delay(
    attempt: int,
    *,
    base: float = DEFAULT_BASE_DELAY,
    cap: float = DEFAULT_MAX_DELAY,
    rng: Callable[[], float] = random.random,
) -> float:
    """Return the backoff wait (seconds) before retry number ``attempt`` (0-based).

    The uncapped delay doubles each attempt (``base``, ``2*base``, ``4*base`` …) and is
    clamped to ``cap``; **full jitter** then scales it by ``rng()`` (expected in
    ``[0, 1)``) so the actual wait is uniformly spread over ``[0, ceiling]``. Passing a
    deterministic ``rng`` (e.g. ``lambda: 1.0`` for the ceiling, ``lambda: 0.0`` for
    zero) makes the schedule exact for tests. ``attempt`` is clamped at 0, and the
    exponent is bounded so an unbounded (retry-forever) loop never overflows.
    """
    safe_attempt = max(0, min(attempt, _MAX_EXPONENT))
    ceiling = min(cap, base * (2.0**safe_attempt))
    return rng() * ceiling
