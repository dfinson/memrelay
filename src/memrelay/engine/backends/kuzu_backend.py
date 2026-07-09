"""Embedded Kuzu backend — preserved as a back-compat fallback (#76, fork D-1).

Kuzu (``kuzu==0.11.3``) is archived and graphiti deprecated its driver, so this is
**not** the default: OOTB installs get LadybugDB and never pull ``kuzu`` (it ships as
the optional ``kuzu`` extra). This backend is kept so anyone with an existing Kuzu
``graph.db`` can still open it by pinning ``backend = "kuzu"`` — a Kuzu-created file
does **not** open under Ladybug (their storage formats diverged; verified in #76), so
there is no in-place migration.

The ``import kuzu``-triggering imports live **inside** :meth:`open_driver`, so merely
importing this module (to run its ``@register``) — or resolving the backend — never
loads the archived, deprecated ``kuzu`` extension.
"""

from __future__ import annotations

import logging
import warnings
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
class KuzuBackend(Backend):
    """Embedded Kuzu storage (archived; back-compat fallback only)."""

    id = "kuzu"

    async def open_driver(self, cfg: Config, *, max_concurrent_queries: int = 1) -> GraphDriver:
        # Imported lazily and locally: the archived native ``kuzu`` extension is only
        # loaded when this fallback is explicitly selected, never on OOTB installs.
        from graphiti_core.driver.kuzu_driver import KuzuDriver

        path = Path(cfg.graph_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with warnings.catch_warnings():
            # KuzuDriver.__init__ emits a DeprecationWarning about the Kuzu backend;
            # it is intentional and only noise here.
            warnings.simplefilter("ignore", DeprecationWarning)
            driver = KuzuDriver(db=str(path), max_concurrent_queries=max_concurrent_queries)

        await apply_graphiti_deltas(driver)
        return driver
