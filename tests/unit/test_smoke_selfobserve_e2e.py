from __future__ import annotations

import asyncio
import time

import pytest
from scripts import smoke_selfobserve_e2e as smoke


class _SlowStoppingIngester:
    def __init__(self) -> None:
        self.cancelled = False
        self.started = False

    def stats(self) -> dict[str, int]:
        return {"spool_pending": 1}

    async def run(self, stop: asyncio.Event) -> None:
        self.started = True
        try:
            await stop.wait()
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def test_drain_timeout_is_one_budget_and_cleans_up_ingester() -> None:
    ingester = _SlowStoppingIngester()
    budget = 0.1

    async def scenario() -> float:
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="single|budget|exceeded"):
            await smoke._drain_once(ingester, timeout=budget)
        return time.monotonic() - started

    elapsed = asyncio.run(scenario())

    assert ingester.started
    assert ingester.cancelled
    assert elapsed < budget * 1.7


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_drain_timeout_cli_rejects_invalid_values(value: str) -> None:
    with pytest.raises(SystemExit):
        smoke.main(["--drain-timeout", value])
