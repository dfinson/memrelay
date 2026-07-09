"""Wiring tests for the cloud opt-in backends — neo4j / falkordb / neptune (#76).

No live servers. Two layers:

1. **Hermetic arg-mapping** (runs everywhere): a fake ``graphiti_core.driver.<x>_driver``
   module is injected into ``sys.modules`` so the adapter's
   ``from graphiti_core.driver.<x>_driver import <X>Driver`` binds a recording ctor.
   That verifies each thin adapter maps ``graph.connection`` onto the right constructor
   args **without** the heavy client libs and **without** anything connecting. It also
   proves the config loader surfaces the nested connection config from env.
2. **CI-only real-module check** (``importorskip``): where the ``falkordb``/``neptune``
   extras are installed (neo4j client is a graphiti hard dep), the real graphiti driver
   modules must import and expose the expected ``provider``. Skips cleanly locally.

Fail-loud: a cloud backend selected with required connection config missing raises a
clear ``ValueError`` *before* importing any driver — mirroring #87's fail-loud pattern.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from memrelay.config import load_config
from memrelay.engine.backends import resolve_backend


def _fake_driver_module(class_name: str):
    """Build a fake driver module whose ``class_name`` records its ctor args."""
    module = types.ModuleType(f"fake_{class_name.lower()}")
    captured: dict[str, object] = {}

    class _RecordingDriver:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

    setattr(module, class_name, _RecordingDriver)
    return module, captured, _RecordingDriver


def _open(backend_id: str, cfg):
    return asyncio.run(resolve_backend(backend_id).open_driver(cfg))


# ─── neo4j ───────────────────────────────────────────────────────────────────


def test_neo4j_maps_connection_to_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured, recorder = _fake_driver_module("Neo4jDriver")
    monkeypatch.setitem(sys.modules, "graphiti_core.driver.neo4j_driver", fake)
    cfg = load_config(
        environ={},
        graph={
            "backend": "neo4j",
            "connection": {
                "uri": "bolt://neo:7687",
                "user": "neo",
                "password": "sekret",
                "database": "prod",
            },
        },
    )
    driver = _open("neo4j", cfg)
    assert isinstance(driver, recorder)
    assert captured["args"] == ("bolt://neo:7687", "neo", "sekret")
    assert captured["kwargs"] == {"database": "prod"}


def test_neo4j_database_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured, _ = _fake_driver_module("Neo4jDriver")
    monkeypatch.setitem(sys.modules, "graphiti_core.driver.neo4j_driver", fake)
    cfg = load_config(
        environ={},
        graph={"backend": "neo4j", "connection": {"uri": "bolt://h:7687"}},
    )
    _open("neo4j", cfg)
    assert captured["args"] == ("bolt://h:7687", None, None)
    assert captured["kwargs"] == {"database": "neo4j"}


def test_neo4j_connection_surfaces_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The config loader must surface [graph.connection] from MEMRELAY_GRAPH__CONNECTION__*.
    fake, captured, _ = _fake_driver_module("Neo4jDriver")
    monkeypatch.setitem(sys.modules, "graphiti_core.driver.neo4j_driver", fake)
    cfg = load_config(
        environ={
            "MEMRELAY_GRAPH__BACKEND": "neo4j",
            "MEMRELAY_GRAPH__CONNECTION__URI": "bolt://env-host:7687",
            "MEMRELAY_GRAPH__CONNECTION__USER": "envuser",
            "MEMRELAY_GRAPH__CONNECTION__PASSWORD": "envpass",
        }
    )
    assert cfg.graph.backend == "neo4j"
    _open("neo4j", cfg)
    assert captured["args"] == ("bolt://env-host:7687", "envuser", "envpass")
    assert captured["kwargs"] == {"database": "neo4j"}


def test_neo4j_missing_uri_fails_loud() -> None:
    cfg = load_config(environ={}, graph={"backend": "neo4j"})
    with pytest.raises(ValueError, match="graph.connection.uri"):
        _open("neo4j", cfg)


# ─── falkordb ──────────────────────────────────────────────────────────────────


def test_falkordb_maps_connection_to_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured, recorder = _fake_driver_module("FalkorDriver")
    monkeypatch.setitem(sys.modules, "graphiti_core.driver.falkordb_driver", fake)
    cfg = load_config(
        environ={},
        graph={
            "backend": "falkordb",
            "connection": {
                "host": "falkor.example",
                "port": 6380,
                "username": "u",
                "password": "p",
                "database": "graph7",
            },
        },
    )
    driver = _open("falkordb", cfg)
    assert isinstance(driver, recorder)
    assert captured["args"] == ()
    assert captured["kwargs"] == {
        "host": "falkor.example",
        "port": 6380,
        "username": "u",
        "password": "p",
        "database": "graph7",
    }


def test_falkordb_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured, _ = _fake_driver_module("FalkorDriver")
    monkeypatch.setitem(sys.modules, "graphiti_core.driver.falkordb_driver", fake)
    cfg = load_config(
        environ={},
        graph={"backend": "falkordb", "connection": {"host": "localhost"}},
    )
    _open("falkordb", cfg)
    assert captured["kwargs"] == {
        "host": "localhost",
        "port": 6379,
        "username": None,
        "password": None,
        "database": "default_db",
    }


def test_falkordb_port_coerced_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Env values arrive as strings; the loader coerces the port to int before it reaches
    # the FalkorDB ctor (which expects int).
    fake, captured, _ = _fake_driver_module("FalkorDriver")
    monkeypatch.setitem(sys.modules, "graphiti_core.driver.falkordb_driver", fake)
    cfg = load_config(
        environ={
            "MEMRELAY_GRAPH__BACKEND": "falkordb",
            "MEMRELAY_GRAPH__CONNECTION__HOST": "fh",
            "MEMRELAY_GRAPH__CONNECTION__PORT": "6380",
        }
    )
    _open("falkordb", cfg)
    assert captured["kwargs"]["port"] == 6380
    assert isinstance(captured["kwargs"]["port"], int)


def test_falkordb_missing_host_fails_loud() -> None:
    cfg = load_config(environ={}, graph={"backend": "falkordb"})
    with pytest.raises(ValueError, match="graph.connection.host"):
        _open("falkordb", cfg)


# ─── neptune ───────────────────────────────────────────────────────────────────


def test_neptune_maps_connection_to_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured, recorder = _fake_driver_module("NeptuneDriver")
    monkeypatch.setitem(sys.modules, "graphiti_core.driver.neptune_driver", fake)
    cfg = load_config(
        environ={},
        graph={
            "backend": "neptune",
            "connection": {
                "host": "neptune-db://cluster.example",
                "aoss_host": "search.example",
                "port": 9999,
                "aoss_port": 8443,
            },
        },
    )
    driver = _open("neptune", cfg)
    assert isinstance(driver, recorder)
    assert captured["args"] == ("neptune-db://cluster.example", "search.example")
    assert captured["kwargs"] == {"port": 9999, "aoss_port": 8443}


def test_neptune_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured, _ = _fake_driver_module("NeptuneDriver")
    monkeypatch.setitem(sys.modules, "graphiti_core.driver.neptune_driver", fake)
    cfg = load_config(
        environ={},
        graph={
            "backend": "neptune",
            "connection": {"host": "neptune-graph://g", "aoss_host": "s"},
        },
    )
    _open("neptune", cfg)
    assert captured["args"] == ("neptune-graph://g", "s")
    assert captured["kwargs"] == {"port": 8182, "aoss_port": 443}


@pytest.mark.parametrize(
    "connection",
    [None, {"host": "neptune-db://c"}, {"aoss_host": "s"}],
)
def test_neptune_missing_required_fails_loud(connection: dict | None) -> None:
    graph: dict[str, object] = {"backend": "neptune"}
    if connection is not None:
        graph["connection"] = connection
    cfg = load_config(environ={}, graph=graph)
    with pytest.raises(ValueError, match="aoss_host"):
        _open("neptune", cfg)


# ─── CI-only: the real graphiti driver modules import + report their provider ──


_REAL_DRIVER_MODULES = {
    "graphiti_core.driver.neo4j_driver": ("Neo4jDriver", "NEO4J"),
    "graphiti_core.driver.falkordb_driver": ("FalkorDriver", "FALKORDB"),
    "graphiti_core.driver.neptune_driver": ("NeptuneDriver", "NEPTUNE"),
}


@pytest.mark.parametrize("module_name", list(_REAL_DRIVER_MODULES))
def test_real_cloud_driver_module_imports_and_reports_provider(module_name: str) -> None:
    # In CI the falkordb/neptune extras (and neo4j core) are installed, so the graphiti
    # native driver module each adapter imports must resolve and expose the right
    # provider. Locally the extras are absent, so this skips cleanly (no live server is
    # ever contacted — the class ``provider`` is read without constructing the driver).
    class_name, provider_name = _REAL_DRIVER_MODULES[module_name]
    # exc_type=ImportError: these modules *exist* but raise ImportError when their client
    # extra is missing (e.g. FalkorDriver's "pip install graphiti-core[falkordb]"). We
    # want that to skip, not error — and pytest 9.1 requires opting in explicitly.
    module = pytest.importorskip(module_name, exc_type=ImportError)
    from graphiti_core.driver.driver import GraphProvider

    driver_cls = getattr(module, class_name)
    assert driver_cls.provider == GraphProvider[provider_name]
