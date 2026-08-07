"""Unit tests for fixture manifest verification (Story 1.4, AC1).

Matrix covers the story's Testing Requirements: valid, missing, byte-changed,
wrong hash/media/revision, absolute path, `..` escape, symlink escape, license/
provenance absent, prohibited classification, and redistribution denial, plus
Windows-separator, drive-qualified/UNC, and case-variation containment cases.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from memrelay_eval.catalog.fixtures import (
    AUDITED_PUBLIC_LICENSES,
    FixtureVerificationError,
    verify_fixture,
    verify_fixtures,
)

FIXTURE_BYTES = b"synthetic evaluator fixture content\n"


def base_fixture(**overrides: object) -> dict[str, object]:
    fixture: dict[str, object] = {
        "id": "fixture_cccccccccccccccccccccccccccccccc",
        "source_path": "fixtures/example.txt",
        "sha256": "__PLACEHOLDER__",
        "media_type": "text/plain",
        "license": "CC0-1.0",
        "provenance": "synthetic",
        "repository_revision": None,
        "extraction_path": "example.txt",
        "data_classification": "synthetic",
        "redistribution_policy": "allowed",
    }
    fixture.update(overrides)
    return fixture


def write_fixture_file(
    root: Path, relative: str = "fixtures/example.txt", data: bytes = FIXTURE_BYTES
) -> str:
    from hashlib import sha256

    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256(data).hexdigest()


def test_valid_fixture_verifies_and_resolves_its_hash(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path)
    fixture = base_fixture(sha256=digest)

    result = verify_fixture(fixture, tmp_path)

    assert result.verified
    assert result.codes == ()
    assert result.resolved_sha256 == digest
    assert result.provenance == "synthetic"
    assert result.data_classification == "synthetic"


def test_missing_fixture_file_fails_closed(tmp_path: Path) -> None:
    fixture = base_fixture(sha256="d" * 64)

    result = verify_fixture(fixture, tmp_path)

    assert not result.verified
    assert "FIXTURE_MISSING" in result.codes


def test_byte_changed_fixture_fails_hash_verification(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path)
    fixture = base_fixture(sha256=digest)
    (tmp_path / "fixtures" / "example.txt").write_bytes(FIXTURE_BYTES + b"tampered")

    result = verify_fixture(fixture, tmp_path)

    assert not result.verified
    assert "FIXTURE_HASH_MISMATCH" in result.codes


def test_wrong_declared_hash_fails_verification(tmp_path: Path) -> None:
    write_fixture_file(tmp_path)
    fixture = base_fixture(sha256="e" * 64)

    result = verify_fixture(fixture, tmp_path)

    assert not result.verified
    assert "FIXTURE_HASH_MISMATCH" in result.codes


def test_wrong_media_type_fails_verification(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path)
    fixture = base_fixture(sha256=digest, media_type="application/json")

    result = verify_fixture(fixture, tmp_path)

    assert not result.verified
    assert "FIXTURE_MEDIA_TYPE_MISMATCH" in result.codes


def test_non_string_repository_revision_fails_verification(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path)
    fixture = base_fixture(sha256=digest, repository_revision=12345)

    result = verify_fixture(fixture, tmp_path)

    assert not result.verified
    assert "FIXTURE_REVISION_INVALID" in result.codes


@pytest.mark.parametrize(
    "source_path",
    [
        "/etc/outside.txt",
        "C:/outside.txt",
        "C:\\outside.txt",
        "../outside.txt",
        "fixtures/../../outside.txt",
        "fixtures\\..\\..\\outside.txt",
        "\\\\server\\share\\outside.txt",
    ],
)
def test_path_escapes_are_rejected_regardless_of_separator_or_form(
    tmp_path: Path, source_path: str
) -> None:
    fixture = base_fixture(sha256="d" * 64, source_path=source_path)

    result = verify_fixture(fixture, tmp_path)

    assert not result.verified
    assert "FIXTURE_PATH_ESCAPE" in result.codes


def test_windows_separators_resolve_within_the_root(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path, relative="fixtures/nested/example.txt")
    fixture = base_fixture(sha256=digest, source_path="fixtures\\nested\\example.txt")

    result = verify_fixture(fixture, tmp_path)

    assert result.verified


@pytest.mark.skipif(os.name != "nt", reason="symlink escape probe targets Windows reparse points")
def test_symlink_escape_outside_root_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_bytes(b"outside content")
    root = tmp_path / "catalog"
    (root / "fixtures").mkdir(parents=True)
    link_path = root / "fixtures" / "escape.txt"
    try:
        link_path.symlink_to(outside / "secret.txt")
    except OSError:
        pytest.skip("symlink creation is not permitted in this environment")

    fixture = base_fixture(sha256="d" * 64, source_path="fixtures/escape.txt")

    result = verify_fixture(fixture, root)

    assert not result.verified
    assert "FIXTURE_PATH_ESCAPE" in result.codes


def test_case_variation_in_declared_path_is_resolved_literally(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path, relative="fixtures/Example.txt")
    fixture = base_fixture(sha256=digest, source_path="fixtures/Example.txt")

    result = verify_fixture(fixture, tmp_path)

    assert result.verified


def test_absent_provenance_is_prohibited(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path)
    fixture = base_fixture(sha256=digest, provenance=None)

    result = verify_fixture(fixture, tmp_path)

    assert not result.verified
    assert "FIXTURE_PROVENANCE_PROHIBITED" in result.codes


def test_prohibited_data_classification_is_rejected(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path)
    fixture = base_fixture(sha256=digest, data_classification="private")

    result = verify_fixture(fixture, tmp_path)

    assert not result.verified
    assert "FIXTURE_CLASSIFICATION_PROHIBITED" in result.codes


def test_redistribution_denial_is_rejected(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path)
    fixture = base_fixture(sha256=digest, redistribution_policy="denied")

    result = verify_fixture(fixture, tmp_path)

    assert not result.verified
    assert "FIXTURE_REDISTRIBUTION_DENIED" in result.codes


def test_public_provenance_requires_an_audited_license(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path)
    fixture = base_fixture(
        sha256=digest, provenance="public", data_classification="public-license-audited"
    )
    fixture["license"] = "Proprietary-Unaudited"

    result = verify_fixture(fixture, tmp_path)

    assert not result.verified
    assert "FIXTURE_LICENSE_UNAUDITED" in result.codes


def test_public_provenance_with_audited_license_verifies(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path)
    fixture = base_fixture(
        sha256=digest, provenance="public", data_classification="public-license-audited"
    )
    fixture["license"] = "MIT"
    assert "MIT" in AUDITED_PUBLIC_LICENSES

    result = verify_fixture(fixture, tmp_path)

    assert result.verified


def test_verify_fixtures_raises_aggregated_error_when_any_fixture_is_invalid(
    tmp_path: Path,
) -> None:
    digest = write_fixture_file(tmp_path)
    valid = base_fixture(sha256=digest)
    invalid = base_fixture(id="fixture_dddddddddddddddddddddddddddddddd", sha256="e" * 64)

    with pytest.raises(FixtureVerificationError) as raised:
        verify_fixtures([valid, invalid], tmp_path)

    failing_ids = {result.fixture_id for result in raised.value.results if not result.verified}
    assert failing_ids == {"fixture_dddddddddddddddddddddddddddddddd"}


def test_verify_fixtures_returns_all_results_when_every_fixture_is_valid(tmp_path: Path) -> None:
    digest = write_fixture_file(tmp_path)
    fixture = base_fixture(sha256=digest)

    results = verify_fixtures([fixture], tmp_path)

    assert results["fixture_cccccccccccccccccccccccccccccccc"].verified
