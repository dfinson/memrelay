"""Embedded Kuzu backend wiring for graphiti-core 0.29.2.

This module encapsulates two graphiti<->Kuzu integration deltas that were
discovered by inspecting the *installed* ``graphiti-core==0.29.2`` /
``kuzu==0.11.3`` (documented in ``docs/e4-engine-notes.md``). Keeping the
workarounds here means the rest of the engine can treat the driver as if the
upstream Kuzu backend were fully wired.

Delta 1 — ``driver._database``:
    ``Graphiti.add_episode`` runs ``if group_id != self.driver._database:
    self.driver = self.driver.clone(database=group_id)``. That is a Neo4j
    multi-database concept; ``KuzuDriver`` never sets ``_database`` (→
    ``AttributeError``) and its ``clone()`` is a no-op that returns ``self``.
    Setting ``_database = None`` makes the "clone per group_id" branch a safe
    no-op, so ``group_id`` correctly degrades to an in-database property filter
    over a single Kuzu file (exactly SPEC §5.1's "group_id = namespace").

Delta 2 — full-text indexes:
    ``Graphiti.build_indices_and_constraints()`` delegates to
    ``KuzuDriver.build_indices_and_constraints()``, which is a no-op, and
    ``setup_schema()`` only creates node/rel tables. The code that actually
    issues ``CREATE_FTS_INDEX`` (``KuzuGraphMaintenanceOperations``) is not
    wired into the driver in 0.29.2, so the full-text indexes that
    ``add_episode``/search query are never created (→ ``Binder exception: ...
    index edge_name_and_fact``). We create them here, once, at open time.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

from graphiti_core.driver.driver import GraphProvider
from graphiti_core.driver.kuzu_driver import KuzuDriver
from graphiti_core.graph_queries import get_fulltext_indices

logger = logging.getLogger(__name__)

# Kuzu ships full-text search as an extension; loading it is idempotent and is a
# no-op when the bundled build already has it statically linked.
_FTS_EXTENSION_STATEMENTS = ("INSTALL FTS;", "LOAD FTS;")


async def open_kuzu_driver(
    graph_path: str | Path,
    *,
    max_concurrent_queries: int = 1,
) -> KuzuDriver:
    """Open the embedded Kuzu database once and return a ready-to-use driver.

    The database file (and parent directory) is created if missing. The driver
    acquires Kuzu's file-level ``READ_WRITE`` lock in its constructor, so this
    must be called exactly once per process — the daemon is the sole writer
    (SPEC §6.5). ``max_concurrent_queries`` is kept at 1 to match that
    single-writer contract.
    """
    path = Path(graph_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    with warnings.catch_warnings():
        # KuzuDriver.__init__ emits a DeprecationWarning about the Kuzu backend;
        # it is intentional (see docs/e4-engine-notes.md) and only noise here.
        warnings.simplefilter("ignore", DeprecationWarning)
        driver = KuzuDriver(db=str(path), max_concurrent_queries=max_concurrent_queries)

    # Delta 1 (graphiti-core 0.29.2): add_episode compares group_id against
    # ``self.driver._database``, which KuzuDriver never sets → AttributeError.
    # Set it only if unset, so a future graphiti that *does* populate _database
    # is not clobbered. Remove when upstream KuzuDriver sets _database (or drops
    # the Neo4j-style clone-per-database branch).
    if not hasattr(driver, "_database"):
        driver._database = None

    # Delta 2 (graphiti-core 0.29.2): KuzuDriver.build_indices_and_constraints()
    # is a no-op, so the full-text indexes add_episode/search need are never
    # created. Create them here, idempotently. Remove once upstream wires
    # CREATE_FTS_INDEX into KuzuDriver.
    await ensure_fulltext_indices(driver)
    return driver


async def ensure_fulltext_indices(driver: KuzuDriver) -> None:
    """Create the full-text indexes graphiti queries, idempotently.

    Safe to call on both a fresh and a re-opened database: on re-open the
    ``CREATE_FTS_INDEX`` calls raise "already exists", which we swallow.
    """
    for statement in _FTS_EXTENSION_STATEMENTS:
        try:
            await driver.execute_query(statement)
        except Exception as exc:  # noqa: BLE001 - extension may be statically bundled
            logger.debug("Kuzu FTS extension statement %r skipped: %s", statement, exc)

    for query in get_fulltext_indices(GraphProvider.KUZU):
        try:
            await driver.execute_query(query)
        except Exception as exc:  # noqa: BLE001 - index likely already exists on re-open
            logger.debug("Kuzu FTS index creation skipped (already exists?): %s", exc)
