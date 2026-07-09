"""Unit tests for the graph Backend seam + lazy registry (#76).

These run in the **unit** suite and must stay native/client-free: importing the
registry, listing backends, and even *resolving* a backend must not load the embedded
``ladybug`` compiled extension nor any **opt-in** cloud client stack — ``falkordb`` or
Neptune's boto3/opensearch/langchain-aws (the libraries gated behind the pyproject
extras). Those imports are deferred to ``open_driver`` precisely so this holds; the
assertions on ``sys.modules`` below are the executable guarantee of that laziness.

(graphiti-core depends on the ``neo4j`` client unconditionally, so it — and graphiti's
own ``neo4j_driver`` module — load regardless of the selected backend; that is not a
laziness violation and is therefore not asserted against.)
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from memrelay.engine.backends import (
    DEFAULT_BACKEND_ID,
    Backend,
    known_backends,
    resolve_backend,
)


def test_default_backend_is_ladybug() -> None:
    assert DEFAULT_BACKEND_ID == "ladybug"


def test_known_backends_lists_all_without_importing_natives() -> None:
    # known_backends() reads the static id->module map, so it answers for every backend
    # without importing any of them (native/client libs must be absent afterwards).
    assert known_backends() == ["falkordb", "ladybug", "neo4j", "neptune"]


def test_resolve_default_returns_ladybug_backend() -> None:
    backend = resolve_backend()  # no id -> the OOTB default
    assert isinstance(backend, Backend)
    assert type(backend).__name__ == "LadybugBackend"
    assert backend.id == "ladybug"


def test_resolve_explicit_ids() -> None:
    assert type(resolve_backend("ladybug")).__name__ == "LadybugBackend"
    assert resolve_backend("ladybug").id == "ladybug"
    # The cloud opt-ins resolve to their thin adapters (no client import at resolve time).
    assert type(resolve_backend("neo4j")).__name__ == "Neo4jBackend"
    assert resolve_backend("neo4j").id == "neo4j"
    assert type(resolve_backend("falkordb")).__name__ == "FalkorBackend"
    assert resolve_backend("falkordb").id == "falkordb"
    assert type(resolve_backend("neptune")).__name__ == "NeptuneBackend"
    assert resolve_backend("neptune").id == "neptune"


def test_resolving_does_not_load_native_or_client_libs() -> None:
    # This invariant — resolution imports the backend *module* (to run @register) but
    # defers the native/client graph import to open_driver — can only be observed in a
    # FRESH interpreter: other tests in this suite load the ladybug native lib into the
    # shared process, so we assert it in a clean subprocess. Resolving *every* backend
    # must still leave the embedded native lib and the cloud client stacks unloaded.
    code = textwrap.dedent(
        """
        import sys
        from memrelay.engine.backends import known_backends, resolve_backend

        known_backends()
        resolve_backend()             # default -> ladybug
        resolve_backend("ladybug")
        resolve_backend("neo4j")      # cloud adapters import their module, not the driver
        resolve_backend("falkordb")
        resolve_backend("neptune")

        assert "ladybug" not in sys.modules, "resolve imported the ladybug native lib"
        assert "falkordb" not in sys.modules, "resolve imported the falkordb client"
        # The opt-in cloud driver modules hard-import their client stacks at module top,
        # so they must stay unimported at resolve (their import is deferred to
        # open_driver). neo4j is exempt: it is a graphiti-core hard dependency, always
        # installed, and graphiti's driver package pulls its neo4j_driver eagerly.
        for mod in (
            "graphiti_core.driver.falkordb_driver",
            "graphiti_core.driver.neptune_driver",
        ):
            assert mod not in sys.modules, f"resolve imported {mod}"
        print("NATIVE_FREE_OK")
        """
    )
    # Inherit the parent env so the subprocess finds ``memrelay`` (PYTHONPATH=src
    # locally; an editable install in CI).
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert "NATIVE_FREE_OK" in result.stdout


def test_unknown_backend_raises_listing_known_ids() -> None:
    # An id that is genuinely not registered (sqlite is a documented *future* escape
    # hatch, not a current backend) must fail loud, naming the ids that are known.
    with pytest.raises(KeyError) as excinfo:
        resolve_backend("sqlite")
    message = str(excinfo.value)
    assert "sqlite" in message
    # The error is actionable: it names the ids that *are* known.
    for backend_id in ("ladybug", "neo4j", "falkordb", "neptune"):
        assert backend_id in message
