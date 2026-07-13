"""Static guard that memrelay's own metadata caps ``litellm`` below the Rust-only line.

Covers ``docs/SMOKE.md`` **Wall B** (HIGH, install-blocker): ``traceforge-toolkit`` pulls in
``litellm`` and pins it ``litellm>=1.0`` with **no upper bound**, so an uncapped fresh
``pip install`` of memrelay resolves the newest ``litellm``. As of ``1.92.0`` litellm **dropped
its universal ``py3-none-any`` wheel** and now ships **only** Linux ``manylinux_2_28``
(x86_64/aarch64) binary wheels plus an **sdist that compiles native code with Rust/Cargo**. On
Windows, macOS, musl or older-glibc Linux, or any other arch there is no matching wheel, so pip
falls back to that sdist and the install **fails without a Rust toolchain**. ``1.91.3`` is the
last release carrying a universal wheel, so ``<1.92`` resolves Rust-free on every platform.

memrelay defends its own zero-config installability by declaring ``litellm`` as a **direct**,
capped dependency (the durable fix belongs upstream). These assertions are intentionally
**static and offline**: they read ``pyproject.toml`` and do version math with ``packaging`` --
they never run a live ``pip`` resolve -- so CI stays deterministic and network-free. If a future
edit drops the direct declaration or loosens the cap to admit the wheel-less ``1.92+`` line,
this test fails.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: The first litellm release that dropped the universal wheel -- must be excluded by the cap.
FIRST_RUSTONLY_RELEASE = "1.92.0"
#: The last litellm release that still ships a universal py3-none-any wheel -- must be admitted.
LAST_UNIVERSAL_WHEEL = "1.91.3"


def _direct_dependencies() -> list[str]:
    assert PYPROJECT.is_file(), f"pyproject.toml not found at {PYPROJECT}"
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["dependencies"]


def _litellm_requirement() -> Requirement:
    """The single ``litellm`` entry from ``[project].dependencies``, parsed as a Requirement."""
    matches = [
        Requirement(spec)
        for spec in _direct_dependencies()
        if canonicalize_name(Requirement(spec).name) == "litellm"
    ]
    assert matches, (
        "litellm must be a DIRECT dependency of memrelay (in [project].dependencies), not left "
        "purely transitive via traceforge-toolkit -- otherwise pip ignores any cap. See Wall B."
    )
    assert len(matches) == 1, f"litellm should be declared exactly once, found {len(matches)}"
    return matches[0]


def test_litellm_is_declared_as_a_direct_dependency() -> None:
    # Presence (and uniqueness) is the whole point: a transitive-only litellm cannot be capped.
    assert _litellm_requirement().name == "litellm"


def test_litellm_specifier_has_an_explicit_upper_bound_at_or_below_1_92() -> None:
    """Lock the *intent*: there is a real ``<``/``<=`` cap bounding litellm at or below 1.92."""
    spec = _litellm_requirement().specifier
    upper_bounds = [
        s
        for s in spec
        if s.operator in ("<", "<=") and Version(s.version) <= Version(FIRST_RUSTONLY_RELEASE)
    ]
    assert upper_bounds, (
        f"litellm must carry an upper bound at or below {FIRST_RUSTONLY_RELEASE} so pip cannot "
        f"select the wheel-less Rust line; got specifier '{spec}'."
    )


@pytest.mark.parametrize(
    "blocked",
    [
        FIRST_RUSTONLY_RELEASE,  # 1.92.0 -- the exact release that dropped the universal wheel
        "1.92.1",
        "1.93.0",
        "2.0.0",
    ],
)
def test_cap_excludes_the_rust_only_releases(blocked: str) -> None:
    spec = _litellm_requirement().specifier
    assert Version(blocked) not in spec, (
        f"litellm {blocked} ships no universal wheel (Rust sdist off-Linux) and must be "
        f"excluded by the cap; specifier '{spec}' wrongly admits it."
    )


@pytest.mark.parametrize(
    "allowed",
    [
        LAST_UNIVERSAL_WHEEL,  # 1.91.3 -- what <1.92 actually resolves to (proven smoke value)
        "1.91.0",
        "1.90.0",
    ],
)
def test_cap_admits_the_last_universal_wheel_releases(allowed: str) -> None:
    # Proves the cap is not over-tightened into an empty/unsatisfiable set: it still lands on a
    # release that has a universal wheel and installs Rust-free everywhere.
    spec = _litellm_requirement().specifier
    assert Version(allowed) in spec, (
        f"litellm {allowed} has a universal wheel and must remain installable; specifier "
        f"'{spec}' wrongly excludes it."
    )
