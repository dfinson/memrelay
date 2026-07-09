"""The memrelay graph **Backend** seam (#76).

A ``Backend`` is the single thing the engine's construction seam resolves to open
the embedded graph: it turns a :class:`~memrelay.config.Config` into a ready-to-use
graphiti ``GraphDriver``. This is the formal boundary that lets memrelay swap its
*storage* driver (LadybugDB by default, Kuzu as a back-compat fallback) **without**
touching graphiti-core's brain (bitemporal fact model, extraction, dedup, RRF) or
``MemoryEngine``'s frozen public async API / wire shapes — the swap lives strictly
below the engine's construction seam.

The surface is deliberately minimal (fork D-4): everything the engine uses at
runtime — ``provider``, ``execute_query``, ``close``, ``EntityNode.get_by_uuid`` —
already lives on the returned *driver*, unchanged. A backend therefore needs only
an ``id`` (its registry key, echoed in ``health()["backend"]``) and an async
``open_driver(cfg)``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphiti_core.driver.driver import GraphDriver

    from memrelay.config import Config


class Backend(ABC):
    """Opens the embedded graph driver for one storage backend.

    Subclasses set a non-empty ``id`` (the value matched against
    ``cfg.graph.backend``) and implement :meth:`open_driver`. Construction is
    trivial and argument-free so :func:`~memrelay.engine.backends.registry.resolve_backend`
    can instantiate the resolved class directly.
    """

    #: Registry key; matched against ``cfg.graph.backend`` and reported by ``health()``.
    id: str

    @abstractmethod
    async def open_driver(self, cfg: Config) -> GraphDriver:
        """Open (creating files as needed) and return a ready-to-use ``GraphDriver``.

        The driver must be fully wired for graphiti-core 0.29.2 — i.e. with the
        two integration deltas applied (see
        :func:`~memrelay.engine.backends._deltas.apply_graphiti_deltas`) — so the
        engine can inject it straight into ``Graphiti(graph_driver=...)``.
        """
        raise NotImplementedError
