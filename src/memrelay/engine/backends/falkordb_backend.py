"""FalkorDB cloud backend — an opt-in graphiti-native driver adapter (#76).

FalkorDB is a server-based (Redis-module) graph database, so it is **not** the OOTB
default; it is selected with ``backend = "falkordb"`` plus connection config under
``[graph.connection]``. This adapter is intentionally thin: it maps memrelay's
connection config onto graphiti-core's own ``FalkorDriver`` and returns it. That driver
is pure graphiti — it self-builds its indices — so **none** of the Ladybug/KUZU-provider
integration deltas (see :mod:`._deltas`) apply here.

The ``falkordb_driver`` module hard-imports the ``falkordb`` client at module top, which
is why (a) selecting this backend needs the optional ``falkordb`` extra and (b) the
graphiti driver import stays **inside** :meth:`open_driver` so the lazy registry never
pulls ``falkordb`` when a different backend is selected.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from memrelay.engine.backends.base import Backend
from memrelay.engine.backends.registry import register

if TYPE_CHECKING:
    from graphiti_core.driver.driver import GraphDriver

    from memrelay.config import Config

logger = logging.getLogger(__name__)


@register
class FalkorBackend(Backend):
    """Opt-in FalkorDB storage via graphiti-core's native ``FalkorDriver``."""

    id = "falkordb"

    async def open_driver(self, cfg: Config) -> GraphDriver:
        conn = cfg.graph.connection
        if conn is None or not conn.host:
            raise ValueError(
                "graph.backend='falkordb' requires graph.connection.host "
                "(the host where FalkorDB is running, e.g. 'localhost')"
            )

        # Imported lazily so resolving a different backend never imports graphiti's
        # FalkorDB driver module (which hard-imports the optional ``falkordb`` client).
        from graphiti_core.driver.falkordb_driver import FalkorDriver

        return FalkorDriver(
            host=conn.host,
            port=conn.port or 6379,
            username=conn.username,
            password=conn.password,
            database=conn.database or "default_db",
        )
