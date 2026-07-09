"""Unit tests for the graph Backend seam + lazy registry (#76).

These run in the **unit** suite and must stay native-free: importing the registry,
listing backends, and even *resolving* a backend must not load the ``ladybug`` or
``kuzu`` compiled extension (they share one pybind11 module and cannot co-load —
the native import is deferred to ``open_driver`` precisely so this holds). The
assertions on ``sys.modules`` below are the executable guarantee of that laziness.
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


def test_known_backends_lists_both_without_importing_natives() -> None:
    # known_backends() reads the static id->module map, so it must answer for both
    # backends without importing either (native libs must be absent afterwards).
    assert known_backends() == ["kuzu", "ladybug"]
    assert "ladybug" in known_backends()
    assert "kuzu" in known_backends()


def test_resolve_default_returns_ladybug_backend() -> None:
    backend = resolve_backend()  # no id -> the OOTB default
    assert isinstance(backend, Backend)
    assert type(backend).__name__ == "LadybugBackend"
    assert backend.id == "ladybug"


def test_resolve_explicit_ids() -> None:
    assert type(resolve_backend("ladybug")).__name__ == "LadybugBackend"
    assert resolve_backend("ladybug").id == "ladybug"
    # The Kuzu fallback still resolves (back-compat), routed to its own backend.
    assert type(resolve_backend("kuzu")).__name__ == "KuzuBackend"
    assert resolve_backend("kuzu").id == "kuzu"


def test_resolving_does_not_load_native_extensions() -> None:
    # This invariant — resolution imports the backend *module* (to run @register) but
    # defers the native graph import to open_driver — can only be observed in a FRESH
    # interpreter: other tests in this suite load the ladybug native lib into the
    # shared process, so we assert it in a clean subprocess. (ladybug/kuzu share one
    # pybind11 module and cannot co-load, so open_driver, not resolution, owns it.)
    code = textwrap.dedent(
        """
        import sys
        from memrelay.engine.backends import known_backends, resolve_backend

        known_backends()
        resolve_backend()            # default -> ladybug
        resolve_backend("ladybug")
        resolve_backend("kuzu")      # fallback module imports too

        assert "ladybug" not in sys.modules, "resolve imported the ladybug native lib"
        assert "kuzu" not in sys.modules, "resolve imported the kuzu native lib"
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
    with pytest.raises(KeyError) as excinfo:
        resolve_backend("neo4j")
    message = str(excinfo.value)
    assert "neo4j" in message
    # The error is actionable: it names the ids that *are* known.
    assert "ladybug" in message and "kuzu" in message
