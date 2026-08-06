"""The sole RFC 8785 / JCS identity-byte boundary for the evaluator."""

from __future__ import annotations

import hmac
import math
from collections.abc import Mapping
from hashlib import sha256
from typing import Final

import rfc8785

DEFAULT_DIGEST_FIELD: Final = "digest"


class CanonicalizationError(ValueError):
    """A value cannot be represented as RFC 8785 canonical JSON."""


def canonical_bytes(value: object) -> bytes:
    """Return UTF-8 RFC 8785 bytes after rejecting non-JSON values."""

    try:
        return rfc8785.dumps(_normalized_json_value(value))
    except rfc8785.CanonicalizationError as error:
        raise CanonicalizationError(str(error)) from error
    except UnicodeError as error:
        raise CanonicalizationError("strings must contain valid Unicode scalar values") from error


def canonical_digest(
    document: Mapping[str, object], *, digest_field: str = DEFAULT_DIGEST_FIELD
) -> str:
    """Hash the canonical top-level projection with only its digest field omitted."""

    return sha256(canonical_bytes(_digest_projection(document, digest_field))).hexdigest()


def attach_digest(
    document: Mapping[str, object], *, digest_field: str = DEFAULT_DIGEST_FIELD
) -> dict[str, object]:
    """Return a copy of a document with its lowercase SHA-256 digest attached."""

    projected = _digest_projection(document, digest_field)
    result = dict(projected)
    result[digest_field] = sha256(canonical_bytes(projected)).hexdigest()
    return result


def verify_digest(
    document: Mapping[str, object], *, digest_field: str = DEFAULT_DIGEST_FIELD
) -> bool:
    """Verify the declared lowercase digest using the same one-field projection."""

    actual = document.get(digest_field)
    return (
        isinstance(actual, str)
        and len(actual) == 64
        and actual.isascii()
        and actual == actual.lower()
        and all(character in "0123456789abcdef" for character in actual)
        and hmac.compare_digest(actual, canonical_digest(document, digest_field=digest_field))
    )


def _digest_projection(document: Mapping[str, object], digest_field: str) -> dict[str, object]:
    if not isinstance(digest_field, str) or not digest_field:
        raise CanonicalizationError("digest field must be a non-empty string")
    return {key: value for key, value in document.items() if key != digest_field}


def _normalized_json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("NaN and Infinity are forbidden")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("JSON object keys must be strings")
            normalized[key] = _normalized_json_value(nested)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalized_json_value(nested) for nested in value]
    raise CanonicalizationError(f"unsupported JSON value type: {type(value).__name__}")
