"""Unit tests for the guidance merge logic (E10-S3, issue #16).

These pin the three safety guarantees of ``memrelay guidance`` at the pure-logic and
file-apply level: opt-in has no effect here (this module never writes on its own),
idempotency (a re-run is byte-identical), and non-destructiveness (content outside the
marked block is preserved; malformed markers are refused rather than mangled).

Hermetic: pure strings + ``tmp_path``. No config, no home dir, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memrelay.guidance import (
    GUIDANCE_BODY,
    MARKER_END,
    MARKER_START,
    Action,
    MalformedMarkersError,
    apply_guidance,
    merge_guidance,
    render_block,
)

TOOL_NAMES = ("memory_recall", "memory_detail", "memory_note")


def test_render_block_is_fenced_and_cites_the_three_real_tools() -> None:
    block = render_block()
    assert block.startswith(MARKER_START)
    assert block.endswith(MARKER_END)
    assert GUIDANCE_BODY in block
    for tool in TOOL_NAMES:
        assert tool in block
    # Exactly one marker pair — the block itself must be well-formed.
    assert block.count(MARKER_START) == 1
    assert block.count(MARKER_END) == 1


def test_create_when_file_absent() -> None:
    result = merge_guidance(None)
    assert result.action is Action.CREATED
    assert result.new_text == render_block() + "\n"


def test_append_preserves_existing_content_exactly() -> None:
    existing = "# My project notes\n\nSome important content.\n"
    result = merge_guidance(existing)
    assert result.action is Action.APPENDED
    # Every original byte survives, at the front, untouched.
    assert result.new_text.startswith(existing)
    assert render_block() in result.new_text
    assert result.new_text.endswith("\n")


def test_append_when_existing_has_no_trailing_newline() -> None:
    existing = "# Title with no newline"
    result = merge_guidance(existing)
    assert result.action is Action.APPENDED
    assert result.new_text.startswith(existing)
    # A blank-line separator is inserted between prior content and the block.
    assert f"{existing}\n\n{MARKER_START}" in result.new_text


def test_empty_file_gets_just_the_block() -> None:
    result = merge_guidance("")
    assert result.action is Action.APPENDED
    assert result.new_text == render_block() + "\n"


def test_replace_in_place_updates_only_the_block() -> None:
    old_block = f"{MARKER_START}\nSTALE BODY — replace me\n{MARKER_END}"
    existing = f"# Notes\n\n{old_block}\n\n## Trailing section\n"
    result = merge_guidance(existing)

    assert result.action is Action.UPDATED
    assert "STALE BODY" not in result.new_text
    assert render_block() in result.new_text
    # Content on both sides of the block is preserved verbatim.
    assert result.new_text.startswith("# Notes\n\n")
    assert result.new_text.endswith("\n\n## Trailing section\n")
    # Still exactly one block after the update (no duplication).
    assert result.new_text.count(MARKER_START) == 1
    assert result.new_text.count(MARKER_END) == 1


def test_rerun_on_current_block_is_unchanged() -> None:
    current = render_block() + "\n"
    result = merge_guidance(current)
    assert result.action is Action.UNCHANGED
    assert result.new_text == current


def test_append_then_rerun_is_byte_identical() -> None:
    existing = "# Notes\n\ncontent\n"
    once = merge_guidance(existing)
    assert once.action is Action.APPENDED
    twice = merge_guidance(once.new_text)
    assert twice.action is Action.UNCHANGED
    assert twice.new_text == once.new_text


@pytest.mark.parametrize(
    "existing",
    [
        f"{MARKER_START}\nbody, no end marker\n",  # start only
        f"body\n{MARKER_END}\n",  # end only
        f"{MARKER_START}\na\n{MARKER_END}\n{MARKER_START}\nb\n{MARKER_END}\n",  # duplicated
        f"{MARKER_END}\nbody\n{MARKER_START}\n",  # out of order
    ],
)
def test_malformed_markers_are_refused(existing: str) -> None:
    with pytest.raises(MalformedMarkersError):
        merge_guidance(existing)


def test_apply_creates_file_and_returns_result(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    result = apply_guidance(path, write=True)
    assert result.action is Action.CREATED
    assert path.read_text(encoding="utf-8") == render_block() + "\n"
    for tool in TOOL_NAMES:
        assert tool in path.read_text(encoding="utf-8")


def test_apply_write_false_does_not_touch_disk(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    result = apply_guidance(path, write=False)
    assert result.action is Action.CREATED  # what it *would* do
    assert not path.exists()  # ...but nothing was written


def test_apply_rerun_leaves_file_byte_identical(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    apply_guidance(path, write=True)
    first_bytes = path.read_bytes()
    second = apply_guidance(path, write=True)
    assert second.action is Action.UNCHANGED
    assert path.read_bytes() == first_bytes


def test_apply_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / ".github" / "copilot-instructions.md"
    result = apply_guidance(path, write=True)
    assert result.action is Action.CREATED
    assert path.is_file()


def test_apply_refuses_to_mutate_a_malformed_file(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    original = f"# Notes\n\n{MARKER_START}\ndangling start\n"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(MalformedMarkersError):
        apply_guidance(path, write=True)
    # The malformed file is left exactly as it was.
    assert path.read_text(encoding="utf-8") == original
