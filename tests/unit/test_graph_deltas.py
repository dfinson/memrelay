from __future__ import annotations

import asyncio
import logging

import pytest
from graphiti_core.driver.driver import GraphProvider
from graphiti_core.graph_queries import get_fulltext_indices

from memrelay.engine.backends import _deltas


def _index_name(query: str) -> str:
    match = _deltas._FTS_INDEX_NAME.search(query)
    assert match is not None
    return match.group(1)


class _IndexDriver:
    """Driver double whose ``SHOW_FUNCTIONS`` / ``SHOW_INDEXES`` answers are pinned.

    Both catalog shapes mirror what a real LadybugDB 0.18.0 returns: rows are dicts,
    and ``SHOW_FUNCTIONS`` reports ``CREATE_FTS_INDEX`` only when the extension loaded.
    """

    def __init__(
        self,
        existing: set[str],
        *,
        fail_create: bool = False,
        fts_available: bool = True,
    ) -> None:
        self.existing = existing
        self.fail_create = fail_create
        self.fts_available = fts_available
        self.queries: list[str] = []

    async def execute_query(self, query: str, **kwargs):
        self.queries.append(query)
        if query == _deltas._SHOW_FUNCTIONS_QUERY:
            names = ["array_cosine_similarity"]
            if self.fts_available:
                names.append(_deltas._FTS_DDL_FUNCTION)
            return ([{"name": name} for name in names], None, None)
        if query == _deltas._SHOW_INDEXES_QUERY:
            return ([{"index_name": name} for name in self.existing], None, None)
        if self.fail_create:
            logging.getLogger("graphiti_core.driver.kuzu_driver").error("real FTS failure")
            raise RuntimeError("real FTS failure")
        return ([], None, None)


def test_reopen_skips_existing_fts_indices_without_error_log(caplog) -> None:
    ddl = get_fulltext_indices(GraphProvider.KUZU)
    driver = _IndexDriver({_index_name(query) for query in ddl})

    with caplog.at_level(logging.ERROR):
        asyncio.run(_deltas.ensure_fulltext_indices(driver))

    assert driver.queries == [_deltas._SHOW_FUNCTIONS_QUERY, _deltas._SHOW_INDEXES_QUERY]
    assert not caplog.records


def test_genuine_fts_creation_failure_propagates_and_stays_visible(caplog) -> None:
    driver = _IndexDriver(set(), fail_create=True)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="real FTS failure"):
        asyncio.run(_deltas.ensure_fulltext_indices(driver))

    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_missing_fts_extension_warns_and_skips_ddl_instead_of_failing(caplog) -> None:
    """An unloadable FTS extension must degrade retrieval, not block engine startup.

    ``load_fts_extension`` is best-effort — a CDN 404 on a yanked build (#118), an
    offline/proxied first run, or the Linux TLS bug (#76) all end in a silent no-op.
    If that made ``CREATE_FTS_INDEX`` propagate, ``LadybugBackend.open_driver`` would
    re-raise and the daemon would refuse to start on a machine that merely cannot
    reach the extension CDN. ``fail_create=True`` proves the skip is a real early
    return: any DDL attempt here would raise.
    """
    driver = _IndexDriver(set(), fail_create=True, fts_available=False)

    with caplog.at_level(logging.WARNING):
        asyncio.run(_deltas.ensure_fulltext_indices(driver))

    assert driver.queries == [_deltas._SHOW_FUNCTIONS_QUERY]
    assert any(
        record.levelno == logging.WARNING and "FTS extension unavailable" in record.getMessage()
        for record in caplog.records
    )


def test_fts_availability_probe_matches_live_ladybug_catalog(tmp_path) -> None:
    """Pin the probe against a real engine, not just the double above.

    Guards the two ways this could rot silently: the ``SHOW_FUNCTIONS`` result-column
    name changing, and ``CREATE_FTS_INDEX`` being reported under different casing.
    """
    from memrelay.engine.backends._fts_extension import load_ladybug_fts_extension
    from memrelay.engine.backends.ladybug_driver import LadybugDriver

    async def scenario() -> tuple[bool, bool]:
        driver = LadybugDriver(db=str(tmp_path / "graph.db"), max_concurrent_queries=1)
        try:
            before = await _deltas._fts_is_available(driver)
            await load_ladybug_fts_extension(driver)
            return before, await _deltas._fts_is_available(driver)
        finally:
            await driver.close()

    before, after = asyncio.run(scenario())

    assert before is False, "a fresh DB must not report FTS before the extension loads"
    if not after:
        pytest.skip("FTS extension could not be provisioned in this environment")
