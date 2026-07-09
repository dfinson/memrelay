"""Backend registry with **lazy** resolution (#76).

memrelay resolves a :class:`~memrelay.engine.backends.base.Backend` from
``cfg.graph.backend`` — ``"ladybug"`` (the OOTB default) or ``"kuzu"`` (a
back-compat fallback). A backend joins the registry by decorating its class with
:func:`register`, mirroring the provider registry (#70) and host-process registry
(#87)::

    from memrelay.engine.backends.registry import register

    @register
    class LadybugBackend(Backend):
        id = "ladybug"
        ...

**Why this registry is lazy, unlike the provider registry.** Provider modules are
cheap to import, so :mod:`memrelay.providers.registry` eagerly ``pkgutil``-discovers
them. Backend modules are **not**: each pulls a compiled native graph extension, and
Ladybug and Kuzu *share the same pybind11 extension* — importing both in one process
raises ``generic_type: type "Database" is already registered`` (verified in #76). So
resolution must import **only** the selected backend's module. The id→module map is
kept static (not derived by import) so :func:`known_backends` can answer without
loading any native library.
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
    "kuzu": "kuzu_backend",
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

    Imports **only** the selected backend's module, so the archived ``kuzu`` package
    is never loaded unless ``backend="kuzu"`` is explicitly requested. Raises
    :class:`KeyError` (listing :func:`known_backends`) for an unknown id.
    """
    resolved = backend_id or DEFAULT_BACKEND_ID
    backend_cls = _REGISTRY.get(resolved) or _load(resolved)
    return backend_cls()
