"""Backend registry with **lazy** resolution (#76).

memrelay resolves a :class:`~memrelay.engine.backends.base.Backend` from
``cfg.graph.backend``: ``"ladybug"`` (the embedded, zero-config OOTB default) or one of
the cloud opt-ins ``"neo4j"`` / ``"falkordb"`` / ``"neptune"``. A backend joins the
registry by decorating its class with :func:`register`, mirroring the provider registry
(#70) and host-process registry (#87)::

    from memrelay.engine.backends.registry import register

    @register
    class LadybugBackend(Backend):
        id = "ladybug"
        ...

**Why this registry is lazy, unlike the provider registry.** Provider modules are
cheap to import, so :mod:`memrelay.providers.registry` eagerly ``pkgutil``-discovers
them. Backend modules are **not**: the embedded default pulls a compiled native graph
extension (``ladybug``), and each cloud backend module hard-imports its own heavy client
stack at module top (``falkordb``; ``boto3``/``opensearch-py``/``langchain-aws`` for
Neptune) that a default ``pip install memrelay`` never installs. So resolution must
import **only** the selected backend's module, so an OOTB (Ladybug) install never needs
any cloud client library. The id→module map is kept static (not derived by import) so
:func:`known_backends` can answer without importing any backend.
"""

from __future__ import annotations

import importlib

from memrelay.engine.backends.base import Backend

#: The OOTB storage backend (LadybugDB) — used when ``cfg.graph.backend`` is unset.
DEFAULT_BACKEND_ID = "ladybug"

#: id -> submodule (within this package) that defines and ``@register``\ s the backend.
#: Static on purpose: :func:`known_backends` reads it without importing native graph
#: libs (which are mutually exclusive within a process).
_BACKEND_MODULES: dict[str, str] = {
    "ladybug": "ladybug_backend",
    "neo4j": "neo4j_backend",
    "falkordb": "falkordb_backend",
    "neptune": "neptune_backend",
}

#: Populated by :func:`register` as backend modules are lazily imported.
_REGISTRY: dict[str, type[Backend]] = {}


def register(backend_cls: type[Backend]) -> type[Backend]:
    """Register ``backend_cls`` under its ``id`` (idempotent); returns the class.

    Usable as a decorator (``@register``). The class is returned unchanged.
    """
    backend_id = getattr(backend_cls, "id", None)
    if not isinstance(backend_id, str) or not backend_id:
        raise ValueError(f"{backend_cls!r} must define a non-empty str `id` to register")
    _REGISTRY[backend_id] = backend_cls
    return backend_cls


def known_backends() -> list[str]:
    """Registered backend ids, sorted — resolved **without importing** native libs."""
    return sorted(_BACKEND_MODULES)


def normalize_backend_id(backend_id: str | None) -> str:
    """Canonicalize a configured backend id — the **single** normalization seam.

    Both the engine (:func:`resolve_backend`) and the CLI preflight
    (``cli._prefetch_fts_extension``) route the raw ``cfg.graph.backend`` through
    *this* function so they can never disagree: an id one accepts, the other accepts,
    and both map it to the same canonical key. A config value like ``"Ladybug"``,
    ``"Neo4j"`` or ``" ladybug "`` (surrounding whitespace) is folded to its registry
    key; an empty / whitespace-only / ``None`` id means the OOTB default (Ladybug).
    Normalization is ``strip()`` + ``casefold()``; the registry's known ids
    (:data:`_BACKEND_MODULES`) remain the single source of truth for validity.
    """
    if backend_id is None:
        return DEFAULT_BACKEND_ID
    return backend_id.strip().casefold() or DEFAULT_BACKEND_ID


def _load(backend_id: str) -> type[Backend]:
    """Lazily import the module that defines ``backend_id`` and return its class."""
    module = _BACKEND_MODULES.get(backend_id)
    if module is None:
        raise KeyError(f"unknown graph backend {backend_id!r}; known: {known_backends()}")
    # Importing the module runs its ``@register`` decorator.
    importlib.import_module(f"{__package__}.{module}")
    try:
        return _REGISTRY[backend_id]
    except KeyError:  # pragma: no cover - a module that fails to self-register is a bug
        raise RuntimeError(
            f"backend module {module!r} did not register id {backend_id!r}"
        ) from None


def resolve_backend(backend_id: str | None = None) -> Backend:
    """Resolve and construct the backend for ``backend_id`` (default: Ladybug).

    The id is canonicalized via :func:`normalize_backend_id` (strip + casefold) so
    ``"Ladybug"`` / ``"Neo4j"`` / ``" ladybug "`` resolve exactly like their registry
    keys — keeping the engine in agreement with the CLI preflight, which normalizes the
    same way. Imports **only** the selected backend's module, so a default (Ladybug)
    install never loads any cloud client library. Raises :class:`KeyError` (listing
    :func:`known_backends`) for an unknown id.
    """
    resolved = normalize_backend_id(backend_id)
    backend_cls = _REGISTRY.get(resolved) or _load(resolved)
    return backend_cls()
