"""The two graphiti<->embedded-graph integration deltas, shared by every backend (#76).

These were discovered against the installed ``graphiti-core==0.29.2`` (originally
for Kuzu; documented in ``docs/e4-engine-notes.md``). They apply **identically** to
LadybugDB because Ladybug *is* Kuzu's codebase and speaks the same Cypher/DDL/FTS —
empirically confirmed in #76 (``INSTALL FTS;``/``LOAD FTS;`` + ``CREATE_FTS_INDEX``
run unchanged). Keeping them here, driver-agnostic, means both ``LadybugBackend`` and
``KuzuBackend`` open a fully-wired driver from a single source of truth.

This module is intentionally **free of any native graph import** (it touches only the
``GraphProvider`` enum and the query-string helper), so importing it never loads
``ladybug`` or ``kuzu`` — the two share a compiled extension and cannot coexist in one
process.

Delta 1 — ``driver._database``:
    ``Graphiti.add_episode`` runs ``if group_id != self.driver._database:
    self.driver = self.driver.clone(database=group_id)``. That is a Neo4j
    multi-database concept; the Kuzu/Ladybug driver never sets ``_database`` (→
    ``AttributeError``) and its ``clone()`` is a no-op that returns ``self``.
    Setting ``_database = None`` makes the "clone per group_id" branch a safe no-op,
    so ``group_id`` correctly degrades to an in-database property filter over a single
    embedded file (exactly SPEC §5.1's "group_id = namespace").

Delta 2 — full-text indexes:
    ``Graphiti.build_indices_and_constraints()`` and the driver's
    ``setup_schema()`` never issue ``CREATE_FTS_INDEX`` in 0.29.2, so the full-text
    indexes ``add_episode``/search query are otherwise never created (→ ``Binder
    exception: ... index edge_name_and_fact``). We create them here, once, at open time.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from graphiti_core.driver.driver import GraphProvider
from graphiti_core.graph_queries import get_fulltext_indices

if TYPE_CHECKING:
    from graphiti_core.driver.driver import GraphDriver

logger = logging.getLogger(__name__)

# The embedded engine ships full-text search as an extension; loading it is
# idempotent and a no-op when the bundled build already has it statically linked.
_FTS_EXTENSION_STATEMENTS = ("INSTALL FTS;", "LOAD FTS;")

# A callable that makes FTS available on ``driver`` (loads the extension). The
# default (``load_fts_extension_native``) uses the engine's own ``INSTALL FTS``;
# the Ladybug backend injects a Python-prefetch variant that is robust to its
# native downloader's Linux TLS bug (see ``_fts_extension``).
FtsExtensionLoader = Callable[["GraphDriver"], Awaitable[None]]


async def apply_graphiti_deltas(
    driver: GraphDriver,
    *,
    load_fts_extension: FtsExtensionLoader | None = None,
) -> None:
    """Apply Delta 1 + Delta 2 to a freshly opened Kuzu/Ladybug driver, in place.

    ``load_fts_extension`` overrides how the FTS extension is made available before
    the indexes are built; when ``None`` the native ``INSTALL FTS; LOAD FTS;`` path
    is used (correct for Kuzu and for any build that bundles FTS statically).
    """
    # Delta 1: set ``_database`` only if unset, so a future graphiti that *does*
    # populate it is not clobbered.
    if not hasattr(driver, "_database"):
        driver._database = None

    # Delta 2a: make the FTS extension available (backend-specific strategy).
    loader = load_fts_extension or load_fts_extension_native
    await loader(driver)

    # Delta 2b: create the full-text indexes graphiti queries, idempotently.
    await ensure_fulltext_indices(driver)


async def load_fts_extension_native(driver: GraphDriver) -> None:
    """Load FTS via the engine's own ``INSTALL FTS; LOAD FTS;`` (best-effort).

    Each statement is a no-op when FTS is already loaded or statically bundled, so
    failures are swallowed at debug level. This is the correct path for Kuzu and
    the fallback for Ladybug (see ``_fts_extension.load_ladybug_fts_extension``).
    """
    for statement in _FTS_EXTENSION_STATEMENTS:
        try:
            await driver.execute_query(statement)
        except Exception as exc:  # noqa: BLE001 - extension may be statically bundled
            logger.debug("FTS extension statement %r skipped: %s", statement, exc)


async def ensure_fulltext_indices(driver: GraphDriver) -> None:
    """Create the full-text indexes graphiti queries, idempotently.

    Assumes the FTS extension is already loaded (see ``apply_graphiti_deltas``).
    Safe to call on both a fresh and a re-opened database: on re-open the
    ``CREATE_FTS_INDEX`` calls raise "already exists", which we swallow. The index
    DDL is provider-keyed on ``GraphProvider.KUZU`` because Ladybug reports (and
    speaks) that dialect deliberately (#76).
    """
    for query in get_fulltext_indices(GraphProvider.KUZU):
        try:
            await driver.execute_query(query)
        except Exception as exc:  # noqa: BLE001 - index likely already exists on re-open
            logger.debug("FTS index creation skipped (already exists?): %s", exc)
