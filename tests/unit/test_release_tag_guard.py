"""Static guard that ``release.yml`` gates publish on the git tag matching the built version.

Covers rt-release F1 (HIGH): ``install-verify`` only proves the built wheel is *internally*
consistent (``memrelay --version`` == its own installed metadata, i.e. 0.1.0 == 0.1.0). It
does NOT compare the git tag to the built version, so a release tagged ``v0.2.0`` while
``src/memrelay/__init__.py`` still names ``0.1.0`` would build a 0.1.0 wheel, pass
install-verify, and publish 0.1.0 to PyPI under a ``v0.2.0`` release -- a silent tag/version
drift shipped to users. The guard strips a leading ``v`` from ``GITHUB_REF_NAME`` and compares
it to the built wheel's version, failing ``install-verify`` (which ``publish-pypi`` ``needs``)
on drift, before any publish job runs.

These assertions are intentionally static (they do not execute the workflow): they pin the
guard's existence, its release-only gating, the exact strip-and-compare, and that publish stays
downstream of the job the guard lives in -- so a future edit that silently drops or loosens the
guard fails CI here. The final case set mirrors the ``${GITHUB_REF_NAME#v}`` == version rule in
pure Python so the compare semantics are documented and locked without spinning a real release.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"

GUARD_STEP_NAME = "Guard the release tag against the built version"


def _release_yml() -> str:
    assert RELEASE_YML.is_file(), f"release workflow not found at {RELEASE_YML}"
    return RELEASE_YML.read_text(encoding="utf-8")


def _guard_step_block(text: str) -> str:
    """The guard step's YAML text, from its ``- name:`` up to the next step or job key."""
    marker = f"- name: {GUARD_STEP_NAME}"
    start = text.find(marker)
    assert start != -1, f"guard step '{GUARD_STEP_NAME}' is missing from release.yml"
    rest = text[start + len(marker) :]
    # End the block at the next step (`- name:`) or the next top-level job (a 2-space key).
    match = re.search(r"\n\s*- name:|\n {2}\w[\w-]*:", rest)
    return rest[: match.start()] if match else rest


def test_release_workflow_defines_release_gated_tag_guard() -> None:
    block = _guard_step_block(_release_yml())

    # Gated to the release event only: a no-op / skipped on push / pull_request /
    # workflow_dispatch, where GITHUB_REF_NAME is a branch name rather than a tag.
    assert "if: github.event_name == 'release'" in block, (
        f"tag guard must be gated on the release event only:\n{block}"
    )
    # Strips a single leading 'v' from the git tag (vX.Y.Z -> X.Y.Z).
    assert "${GITHUB_REF_NAME#v}" in block, (
        f"tag guard must strip a leading 'v' from GITHUB_REF_NAME:\n{block}"
    )
    # Compares the stripped tag against the built wheel's version and fails hard on mismatch.
    assert "meta_version" in block, f"guard must compare against the built version:\n{block}"
    assert 'if [ "$tag_version" != "$meta_version" ]' in block, (
        f"guard must fail when the stripped tag != built version:\n{block}"
    )
    assert "::error::" in block and "exit 1" in block, (
        f"guard must emit ::error:: and exit non-zero on mismatch:\n{block}"
    )


def test_publish_is_gated_on_the_job_that_carries_the_guard() -> None:
    """``publish-pypi`` must stay downstream of ``install-verify`` (which hosts the guard), so a
    failed guard blocks the real PyPI publish before it can run."""
    text = _release_yml()

    # The guard lives in install-verify; the publish jobs `needs` it, so publish is gated.
    assert "needs: [build, install-verify]" in text, (
        "publish jobs must `needs` install-verify so the tag guard gates publish"
    )
    # The guard step sits inside install-verify, ahead of the first publish job.
    install_verify = text.find("\n  install-verify:")
    guard = text.find(GUARD_STEP_NAME)
    first_publish = text.find("\n  publish-testpypi:")
    assert -1 < install_verify < guard < first_publish, (
        "the tag guard must live inside the install-verify job, ahead of the publish jobs"
    )


def _strip_and_compare(ref_name: str, built_version: str) -> bool:
    """Pure-Python mirror of the workflow's ``${GITHUB_REF_NAME#v}`` == ``meta_version`` check."""
    tag_version = ref_name[1:] if ref_name.startswith("v") else ref_name
    return tag_version == built_version


@pytest.mark.parametrize(
    ("ref_name", "built", "expected_ok"),
    [
        ("v0.2.0", "0.2.0", True),  # canonical vX.Y.Z tag matching the built version
        ("0.2.0", "0.2.0", True),  # bare tag (no leading v) is tolerated
        ("v1.10.0", "1.10.0", True),  # multi-digit segments strip correctly
        ("v0.2.0", "0.1.0", False),  # THE drift the guard exists to catch
        ("v0.1.0", "0.2.0", False),  # inverse drift
        ("vv0.1.0", "0.1.0", False),  # only ONE leading 'v' is stripped
    ],
)
def test_tag_strip_and_compare_semantics(ref_name: str, built: str, expected_ok: bool) -> None:
    assert _strip_and_compare(ref_name, built) is expected_ok
