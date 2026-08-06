"""Compatibility imports for the evaluator's shared RFC 8785/JCS boundary."""

from __future__ import annotations

from memrelay_eval.canonical import (
    DEFAULT_DIGEST_FIELD,
    CanonicalizationError,
    attach_digest,
    canonical_bytes,
    canonical_digest,
    verify_digest,
)

__all__ = [
    "DEFAULT_DIGEST_FIELD",
    "CanonicalizationError",
    "attach_digest",
    "canonical_bytes",
    "canonical_digest",
    "verify_digest",
]
