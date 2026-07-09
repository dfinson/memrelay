"""Amazon Neptune cloud backend — an opt-in graphiti-native driver adapter (#76).

Neptune is a managed AWS graph service paired with an OpenSearch (AOSS) endpoint for
full-text search, so it is **not** the OOTB default; it is selected with
``backend = "neptune"`` plus connection config under ``[graph.connection]``. This
adapter is intentionally thin: it maps memrelay's connection config onto graphiti-core's
own ``NeptuneDriver`` and returns it. That driver is pure graphiti — it self-builds its
indices — so **none** of the Ladybug/KUZU-provider integration deltas (see
:mod:`._deltas`) apply here.

The ``neptune_driver`` module hard-imports ``boto3`` / ``opensearch-py`` /
``langchain-aws`` at module top, which is why (a) selecting this backend needs the
optional ``neptune`` extra and (b) the graphiti driver import stays **inside**
:meth:`open_driver` so the lazy registry never pulls that stack when a different backend
is selected. graphiti's ``NeptuneDriver`` requires ``host`` to be a ``neptune-db://`` or
``neptune-graph://`` endpoint and ``aoss_host`` to be the OpenSearch host.
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
class NeptuneBackend(Backend):
    """Opt-in Amazon Neptune storage via graphiti-core's native ``NeptuneDriver``."""

    id = "neptune"

    async def open_driver(self, cfg: Config) -> GraphDriver:
        conn = cfg.graph.connection
        if conn is None or not conn.host or not conn.aoss_host:
            raise ValueError(
                "graph.backend='neptune' requires graph.connection.host "
                "(a 'neptune-db://...' or 'neptune-graph://...' endpoint) and "
                "graph.connection.aoss_host (the OpenSearch endpoint)"
            )

        # Imported lazily so resolving a different backend never imports graphiti's
        # Neptune driver module (which hard-imports boto3/opensearch-py/langchain-aws).
        from graphiti_core.driver.neptune_driver import NeptuneDriver

        return NeptuneDriver(
            conn.host,
            conn.aoss_host,
            port=conn.port or 8182,
            aoss_port=conn.aoss_port or 443,
        )
