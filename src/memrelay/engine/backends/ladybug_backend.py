"""LadybugDB backend — the OOTB default storage backend (#76).

Opens the embedded Ladybug graph once and returns a fully graphiti-wired driver.
The driver acquires the engine's file-level ``READ_WRITE`` lock in its constructor,
so ``open_driver`` must be called exactly once per process — the daemon is the sole
writer (SPEC §6.5), which is why ``max_concurrent_queries`` stays at 1.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from memrelay.engine.backends._deltas import apply_graphiti_deltas
from memrelay.engine.backends.base import Backend
from memrelay.engine.backends.registry import register

if TYPE_CHECKING:
    from graphiti_core.driver.driver import GraphDriver

    from memrelay.config import Config

logger = logging.getLogger(__name__)


@register
class LadybugBackend(Backend):
    """Embedded LadybugDB storage (the zero-config, no-server default)."""

    id = "ladybug"

    async def open_driver(self, cfg: Config, *, max_concurrent_queries: int = 1) -> GraphDriver:
        # Imported lazily so merely resolving this backend never loads the native
        # Ladybug extension (it is mutually exclusive in-process with Kuzu's).
        from memrelay.engine.backends.ladybug_driver import LadybugDriver

        path = Path(cfg.graph_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        driver = LadybugDriver(db=str(path), max_concurrent_queries=max_concurrent_queries)
        await apply_graphiti_deltas(driver)
        return driver
