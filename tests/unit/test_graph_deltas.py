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
    def __init__(self, existing: set[str], *, fail_create: bool = False) -> None:
        self.existing = existing
        self.fail_create = fail_create
        self.queries: list[str] = []

    async def execute_query(self, query: str, **kwargs):
        self.queries.append(query)
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

    assert driver.queries == [_deltas._SHOW_INDEXES_QUERY]
    assert not caplog.records


def test_genuine_fts_creation_failure_propagates_and_stays_visible(caplog) -> None:
    driver = _IndexDriver(set(), fail_create=True)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="real FTS failure"):
        asyncio.run(_deltas.ensure_fulltext_indices(driver))

    assert any(record.levelno >= logging.ERROR for record in caplog.records)
