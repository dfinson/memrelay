"""Neo4j cloud backend — an opt-in graphiti-native driver adapter (#76).

Neo4j is a server-based graph database (not embedded), so it is **not** the OOTB
default; it is selected with ``backend = "neo4j"`` plus connection config under
``[graph.connection]``. This adapter is intentionally thin: it maps memrelay's
connection config onto graphiti-core's own ``Neo4jDriver`` and returns it. That driver
is pure graphiti — it self-builds its indices/constraints — so **none** of the
Ladybug/KUZU-provider integration deltas (see :mod:`._deltas`) apply here.

graphiti-core depends unconditionally on the ``neo4j`` client (``neo4j>=5.26.0`` is a
hard Requires-Dist), so selecting this backend needs **no** optional extra — only a
reachable server and credentials. The graphiti driver import stays **inside**
:meth:`open_driver` to keep the registry lazy (resolving a *different* backend never
imports it).
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
class Neo4jBackend(Backend):
    """Opt-in Neo4j storage via graphiti-core's native ``Neo4jDriver``."""

    id = "neo4j"

    async def open_driver(self, cfg: Config) -> GraphDriver:
        conn = cfg.graph.connection
        if conn is None or not conn.uri:
            raise ValueError(
                "graph.backend='neo4j' requires graph.connection.uri "
                "(e.g. 'bolt://localhost:7687' or a 'neo4j+s://...' URI)"
            )

        # Imported lazily so resolving a different backend never imports graphiti's
        # Neo4j driver module.
        from graphiti_core.driver.neo4j_driver import Neo4jDriver

        return Neo4jDriver(
            conn.uri,
            conn.user,
            conn.password,
            database=conn.database or "neo4j",
        )
