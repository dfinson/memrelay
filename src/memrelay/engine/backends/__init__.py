"""memrelay graph storage backends (#76).

Importing this package pulls in only the seam (:class:`Backend`) and the lazy
:mod:`registry` — never a concrete backend module, so no native graph library
(``ladybug``/``kuzu``) is loaded until :func:`resolve_backend` selects one.
"""

from __future__ import annotations

from memrelay.engine.backends.base import Backend
from memrelay.engine.backends.registry import (
    DEFAULT_BACKEND_ID,
    known_backends,
    register,
    resolve_backend,
)

__all__ = [
    "DEFAULT_BACKEND_ID",
    "Backend",
    "known_backends",
    "register",
    "resolve_backend",
]
