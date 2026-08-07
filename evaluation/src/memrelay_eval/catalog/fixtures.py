"""Fixture manifest verification: byte, path, and authorization gates (Story 1.4, AC1).

Every declared fixture reference is verified against the bytes actually present on
disk under the governed catalog root before compilation may proceed. A missing,
path-escaping, byte-changed, or unauthorized (prohibited classification, unaudited
public license, denied redistribution, or unrecognized provenance) fixture fails
verification, and `verify_fixtures` raises so the whole compilation fails closed.
This module never performs a live repository fetch: `repository_revision` is
type-checked only, matching the story's explicit scope boundary.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from memrelay_eval.domain.errors import DomainError

#: Fixture-level provenance values this evaluator is authorized to compile.
AUTHORIZED_PROVENANCE = frozenset({"synthetic", "public"})

#: Fixture-level data classifications this evaluator is authorized to compile.
#: Anything else (private, personal, proprietary, credential material, and similar)
#: is mechanically denied, per AD-23 and this story's AC1/AC3.
AUTHORIZED_DATA_CLASSIFICATIONS = frozenset({"synthetic", "public-license-audited"})

#: Redistribution policy values that authorize compiling a fixture at all.
AUTHORIZED_REDISTRIBUTION_POLICIES = frozenset({"allowed"})

#: SPDX license identifiers that have been explicitly audited for public-provenance
#: fixtures. A public fixture outside this allowlist fails verification even if its
#: bytes and path are otherwise valid.
AUDITED_PUBLIC_LICENSES = frozenset(
    {
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "Unlicense",
    }
)


class FixtureVerificationError(DomainError):
    """One or more declared fixtures failed byte, path, or authorization checks."""

    def __init__(self, results: tuple[FixtureVerificationResult, ...]) -> None:
        self.results = results
        failures = tuple(result for result in results if not result.verified)
        super().__init__(
            "; ".join(f"{result.fixture_id}: {result.message}" for result in failures)
            or "fixture verification failed"
        )


@dataclass(frozen=True, slots=True)
class FixtureVerificationResult:
    """The outcome of verifying one declared fixture against catalog-root bytes."""

    fixture_id: str
    verified: bool
    codes: tuple[str, ...]
    message: str
    resolved_sha256: str | None
    provenance: str | None
    data_classification: str | None


def _is_relatively_contained(raw_path: str) -> bool:
    """Reject absolute, drive-qualified, UNC, or traversal-bearing declared paths."""

    if not isinstance(raw_path, str) or not raw_path:
        return False
    normalized = raw_path.replace("\\", "/")
    if normalized.startswith("/"):
        return False
    if ":" in raw_path:
        return False
    segments = normalized.split("/")
    return not any(segment in ("", ".", "..") for segment in segments)


def _resolve_within_root(raw_path: str, catalog_root: Path) -> Path | None:
    """Resolve a declared relative path and reject any escape from the root.

    Resolution follows symlinks and reparse points (`Path.resolve`), so an
    in-bounds-looking relative path that a symlink redirects outside the
    governed root is rejected here even though `_is_relatively_contained`
    already accepted its literal text.
    """

    if not _is_relatively_contained(raw_path):
        return None
    root_resolved = catalog_root.resolve()
    candidate = root_resolved.joinpath(*raw_path.replace("\\", "/").split("/"))
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return resolved


def verify_fixture(fixture: Mapping[str, Any], catalog_root: Path) -> FixtureVerificationResult:
    """Verify one declared fixture's identity, bytes, and authorization."""

    fixture_id = fixture.get("id")
    fixture_id_text = fixture_id if isinstance(fixture_id, str) else "<unidentified fixture>"
    codes: list[str] = []

    source_path = fixture.get("source_path")
    extraction_path = fixture.get("extraction_path")
    provenance = fixture.get("provenance")
    data_classification = fixture.get("data_classification")
    redistribution_policy = fixture.get("redistribution_policy")
    license_value = fixture.get("license")
    declared_sha256 = fixture.get("sha256")
    media_type = fixture.get("media_type")
    repository_revision = fixture.get("repository_revision")

    resolved: Path | None = None
    if not isinstance(source_path, str) or _resolve_within_root(source_path, catalog_root) is None:
        codes.append("FIXTURE_PATH_ESCAPE")
    else:
        resolved = _resolve_within_root(source_path, catalog_root)

    if not isinstance(extraction_path, str) or not _is_relatively_contained(extraction_path):
        codes.append("FIXTURE_EXTRACTION_PATH_INVALID")

    resolved_sha256: str | None = None
    if resolved is not None:
        if not resolved.is_file():
            codes.append("FIXTURE_MISSING")
        else:
            data = resolved.read_bytes()
            resolved_sha256 = sha256(data).hexdigest()
            if not isinstance(declared_sha256, str) or resolved_sha256 != declared_sha256.lower():
                codes.append("FIXTURE_HASH_MISMATCH")
            guessed_media_type, _ = mimetypes.guess_type(source_path)
            if not isinstance(media_type, str) or guessed_media_type != media_type:
                codes.append("FIXTURE_MEDIA_TYPE_MISMATCH")

    if repository_revision is not None and not isinstance(repository_revision, str):
        codes.append("FIXTURE_REVISION_INVALID")

    if not isinstance(provenance, str) or provenance not in AUTHORIZED_PROVENANCE:
        codes.append("FIXTURE_PROVENANCE_PROHIBITED")
    if (
        not isinstance(data_classification, str)
        or data_classification not in AUTHORIZED_DATA_CLASSIFICATIONS
    ):
        codes.append("FIXTURE_CLASSIFICATION_PROHIBITED")
    if (
        not isinstance(redistribution_policy, str)
        or redistribution_policy not in AUTHORIZED_REDISTRIBUTION_POLICIES
    ):
        codes.append("FIXTURE_REDISTRIBUTION_DENIED")
    if provenance == "public" and (
        not isinstance(license_value, str) or license_value not in AUDITED_PUBLIC_LICENSES
    ):
        codes.append("FIXTURE_LICENSE_UNAUDITED")

    verified = not codes
    message = "fixture verified" if verified else "; ".join(codes)
    return FixtureVerificationResult(
        fixture_id=fixture_id_text,
        verified=verified,
        codes=tuple(codes),
        message=message,
        resolved_sha256=resolved_sha256,
        provenance=provenance if isinstance(provenance, str) else None,
        data_classification=data_classification if isinstance(data_classification, str) else None,
    )


def verify_fixtures(
    fixtures: Sequence[Mapping[str, Any]], catalog_root: Path
) -> dict[str, FixtureVerificationResult]:
    """Verify every declared fixture, failing closed if any fixture is invalid."""

    results = {
        (fixture.get("id") if isinstance(fixture.get("id"), str) else str(index)): verify_fixture(
            fixture, catalog_root
        )
        for index, fixture in enumerate(fixtures)
    }
    if any(not result.verified for result in results.values()):
        raise FixtureVerificationError(tuple(results.values()))
    return results
