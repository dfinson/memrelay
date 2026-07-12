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

# NB: ``normalize_backend_id`` is imported *locally* inside the tests that need it (not at
# module top) on purpose — so the mixed-case ``resolve_backend`` counterfactual below can run
# and fail with the genuine ``KeyError`` against the pre-fix code, instead of the whole module
# failing to import because the (new) normalizer symbol doesn't exist yet.


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


# --- backend id normalization: the single seam the engine + CLI share -----------
#
# rt-backends: the backend id was treated case-sensitively by the engine
# (``resolve_backend`` took the raw ``cfg.graph.backend``) while the ``init`` CLI preflight
# lowercased it — so a config like ``"Ladybug"`` / ``"Neo4j"`` / ``" ladybug "`` could pass
# the CLI yet raise ``KeyError`` at engine start. Both sides now route through
# :func:`normalize_backend_id`, so they can never disagree. These tests assert the *correct*
# behavior (the raw id resolves), not the old crash.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ladybug", "ladybug"),
        ("Ladybug", "ladybug"),
        ("LADYBUG", "ladybug"),
        (" ladybug ", "ladybug"),
        ("\tLadyBug\n", "ladybug"),
        ("Neo4j", "neo4j"),
        (" NEO4J ", "neo4j"),
        ("FalkorDB", "falkordb"),
        (" Neptune ", "neptune"),
        # An empty / whitespace-only / missing id means the OOTB default.
        (None, "ladybug"),
        ("", "ladybug"),
        ("   ", "ladybug"),
        # Unknown ids are still normalized (validity is the registry's call, not this seam's).
        ("SQLite", "sqlite"),
    ],
)
def test_normalize_backend_id_folds_case_and_whitespace(raw: str | None, expected: str) -> None:
    from memrelay.engine.backends.registry import normalize_backend_id

    assert normalize_backend_id(raw) == expected


def test_normalize_backend_id_default_is_the_registry_default() -> None:
    from memrelay.engine.backends.registry import normalize_backend_id

    # The empty/None sentinel must fold to the *registry's* declared default, not a literal.
    assert normalize_backend_id(None) == DEFAULT_BACKEND_ID
    assert normalize_backend_id("  ") == DEFAULT_BACKEND_ID


@pytest.mark.parametrize(
    ("raw", "canonical", "cls_name"),
    [
        ("Ladybug", "ladybug", "LadybugBackend"),
        (" ladybug ", "ladybug", "LadybugBackend"),
        ("LADYBUG", "ladybug", "LadybugBackend"),
        ("Neo4j", "neo4j", "Neo4jBackend"),
        (" NEO4J ", "neo4j", "Neo4jBackend"),
        ("FalkorDB", "falkordb", "FalkorBackend"),
        (" Neptune ", "neptune", "NeptuneBackend"),
    ],
)
def test_resolve_backend_accepts_mixed_case_and_whitespace(
    raw: str, canonical: str, cls_name: str
) -> None:
    # The engine hands ``resolve_backend`` the RAW ``cfg.graph.backend``. A mixed-case /
    # whitespace id that the CLI preflight green-lights must resolve here too — pre-fix this
    # raised ``KeyError`` (the case-sensitive lookup), which is the failure this test pins.
    backend = resolve_backend(raw)
    assert isinstance(backend, Backend)
    assert type(backend).__name__ == cls_name
    assert backend.id == canonical
