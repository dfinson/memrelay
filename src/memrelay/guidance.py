"""Opt-in agent-instructions guidance (E10-S3, issue #16).

memrelay stores per-repo memory and serves recall tools over MCP, but an agent only
uses them if its *standing instructions* tell it when and how. This module holds the
guidance text plus the idempotent, non-destructive merge logic behind the
``memrelay guidance`` command, which appends that text into a supported agent
instruction file (``AGENTS.md`` / ``CLAUDE.md`` / ``.github/copilot-instructions.md``).

Design guarantees:

* **Opt-in.** Nothing here writes on its own; the CLI drives every write behind an
  explicit run plus a confirmation (or ``--yes``), and ``--dry-run`` previews only.
* **Idempotent.** The guidance lives inside a fenced, HTML-comment-marked block, so a
  re-run replaces the block *in place* (byte-identical when the text is unchanged)
  rather than appending a duplicate.
* **Non-destructive.** Content outside the markers is preserved exactly; malformed or
  duplicated markers are refused rather than mangled.

Everything is pure text + filesystem: no config load, no home dir, no network — which
keeps the command trivially hermetic to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

#: Fenced markers delimiting memrelay's managed block inside an instruction file. A
#: re-run rewrites only the text between (and including) these, so the block updates in
#: place. Kept as plain HTML comments so they render invisibly in Markdown.
MARKER_START = "<!-- memrelay:guidance:start -->"
MARKER_END = "<!-- memrelay:guidance:end -->"

#: The guidance body rendered between the markers. It cites the EXACT three tools the
#: memrelay MCP server registers (see ``memrelay/mcp/tools.py``): ``memory_recall`` /
#: ``memory_detail`` / ``memory_note`` — nothing invented. These bytes are fixed so a
#: re-run is byte-identical (the idempotency contract), and the ``memory_note`` mention
#: reflects that memory is repo-scoped by default (SPEC namespaces).
GUIDANCE_BODY = """\
## memrelay — persistent memory

This project uses [memrelay](https://github.com/dfinson/memrelay) for persistent,
cross-session memory, served through the `memrelay` MCP server. Memory is scoped to
this repository by default.

**Before you start a task**, call `memory_recall` with a short description of what you
are about to work on to load relevant context from earlier sessions — prior decisions,
conventions, gotchas, and fixes. Recall again whenever you enter an unfamiliar area, a
past decision would help, or you are about to repeat work.

- `memory_recall("<what you're working on>")` — search earlier sessions for relevant context.
- `memory_detail("<node-uuid>")` — expand a specific entity or relationship a recall surfaced.
- `memory_note("<durable fact>")` — store a decision, convention, or gotcha worth recalling later.

**After you finish meaningful work**, call `memory_note` to record decisions, conventions,
or gotchas future sessions should know. Use these tools proactively — you do not need to
ask permission."""


class MalformedMarkersError(ValueError):
    """Raised when an instruction file's memrelay markers are unbalanced/duplicated.

    We refuse to edit such a file rather than risk corrupting it — the user is asked to
    fix or remove the stray markers first.
    """


class Action(StrEnum):
    """What applying the guidance did (or would do) to the target file."""

    CREATED = "created"  # file did not exist; written fresh with just the block
    APPENDED = "appended"  # existing file had no block; block added, content preserved
    UPDATED = "updated"  # existing block replaced in place with new text
    UNCHANGED = "unchanged"  # existing block already byte-identical; nothing to do


@dataclass(frozen=True)
class MergeResult:
    """Outcome of a merge: the action taken and the full resulting file text."""

    action: Action
    new_text: str


@dataclass(frozen=True)
class Target:
    """A supported, repo-local agent instruction file."""

    key: str
    relative_path: str
    label: str


#: The small, well-justified initial target set — all repo-local (version-controlled, so
#: the user sees the diff, and repo-scoped to match memrelay's default namespace). Extend
#: by adding an entry; ``--path`` already covers any other/global file without a code change.
TARGETS: dict[str, Target] = {
    "agents": Target("agents", "AGENTS.md", "cross-agent standard (AGENTS.md)"),
    "claude": Target("claude", "CLAUDE.md", "Claude Code project instructions"),
    "copilot": Target(
        "copilot",
        str(Path(".github") / "copilot-instructions.md"),
        "GitHub Copilot repository instructions",
    ),
}

#: Default target when neither ``--target`` nor ``--path`` is given.
DEFAULT_TARGET = "agents"


def render_block() -> str:
    """Return the full fenced guidance block (start marker, body, end marker)."""
    return f"{MARKER_START}\n{GUIDANCE_BODY}\n{MARKER_END}"


def resolve_target_path(target: str, base_dir: Path) -> Path:
    """Resolve a ``--target`` key to a concrete path under ``base_dir``."""
    return base_dir / TARGETS[target].relative_path


def merge_guidance(existing: str | None) -> MergeResult:
    """Compute the file text after inserting/updating the guidance block (pure).

    ``existing`` is the current file contents, or ``None`` if the file is absent. The
    merge is non-destructive and idempotent:

    * absent → create a file containing just the block;
    * present without markers → append the block, preserving existing bytes exactly;
    * present with one balanced marker pair → replace only the marked region, preserving
      everything outside it (``UNCHANGED`` when already byte-identical);
    * unbalanced/duplicated/out-of-order markers → :class:`MalformedMarkersError`.
    """
    block = render_block()

    if existing is None:
        return MergeResult(Action.CREATED, block + "\n")

    starts = existing.count(MARKER_START)
    ends = existing.count(MARKER_END)

    if starts == 0 and ends == 0:
        if existing == "":
            return MergeResult(Action.APPENDED, block + "\n")
        # Preserve existing bytes verbatim; add a single blank-line separator, then the
        # block. (Only trailing newlines already present are reused — nothing removed.)
        separator = "\n" if existing.endswith("\n") else "\n\n"
        return MergeResult(Action.APPENDED, existing + separator + block + "\n")

    start_index = existing.find(MARKER_START)
    end_index = existing.find(MARKER_END)
    if starts != 1 or ends != 1 or start_index > end_index:
        raise MalformedMarkersError(
            f"{MARKER_START!r}/{MARKER_END!r} appear an unexpected number of times or out "
            "of order; fix or remove the stray memrelay markers and re-run."
        )

    before = existing[:start_index]
    after = existing[end_index + len(MARKER_END) :]
    new_text = before + block + after
    action = Action.UNCHANGED if new_text == existing else Action.UPDATED
    return MergeResult(action, new_text)


def apply_guidance(path: Path, *, write: bool) -> MergeResult:
    """Read ``path``, merge the guidance block, and optionally write the result.

    Returns the :class:`MergeResult` in all cases (so callers can preview with
    ``write=False``). When ``write`` is true and the action is not ``UNCHANGED``, the
    file is written with LF newlines (the repo's committed convention), creating parent
    directories as needed. Raises :class:`MalformedMarkersError` for a file whose markers
    are malformed — never mutating it.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    result = merge_guidance(existing)
    if write and result.action is not Action.UNCHANGED:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.new_text, encoding="utf-8", newline="\n")
    return result
