"""Compose git history into episodes for ``memrelay seed`` (E9-S4 #61).

``seed`` bootstraps memory from a repo's git history so a user gets useful recall on
day one, before live sessions accrue. This module is the **pure, unit-testable core**:
it turns ``git log`` output into :class:`~memrelay.ingest.episode.EpisodeRecord` dicts
bound for the durable spool. Exactly one episode is produced per commit, composed from
the commit metadata (subject, body, author, ISO author date) plus the touched file
paths — deliberately *no* diffs (too noisy) and *no* GitHub API data (needs network).

The design keeps I/O out of composition so the interesting logic is testable without a
real repo:

* :func:`parse_git_log` / :func:`parse_commit_chunk` — pure parsers over the
  control-char-delimited ``git log`` text produced by :func:`git_log_args`.
* :func:`compose_content` — pure commit → episode-content composer.
* :func:`build_record` — pure commit → episode-dict builder. The idempotency key is
  derived from ``(session_id=f"git-seed:{namespace}", event_id=<full sha>, content)``,
  all of which are **stable per commit**, so re-seeding produces byte-identical keys and
  the spool's ``UNIQUE(idempotency_key)`` makes a re-seed a net-zero no-op (the #61
  idempotency AC).
* :func:`stream_git_log` — the only impure function; it runs ``git`` and *streams* its
  output (never buffering the whole history), yielding :class:`GitCommit` objects as
  each commit's record separator is seen. Bounded by ``max_count``.

The module is intentionally pure-stdlib (``subprocess`` only, no new dependency) and
lazy-imports :class:`EpisodeRecord` so importing it stays cheap.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Default bound on how many most-recent commits ``seed`` ingests. Keeps a huge history
#: from blowing up memory / the spool; overridable via ``--max-count``.
DEFAULT_MAX_COUNT = 500

#: How many streamed records the CLI appends per flush. Streaming + a small batch keeps
#: the whole history from being materialized at once (the #61 "bounded/batched" AC).
DEFAULT_BATCH_SIZE = 100

#: Provenance stamped on every seeded episode (E5-S3 #40 threads ``source`` through).
SEED_SOURCE = "git"

# Control characters used to frame ``git log`` output unambiguously. They do not occur in
# commit messages or file paths, so bodies may span multiple lines and still parse:
#   * RS (0x1e) separates commits,
#   * US (0x1f) separates the fixed metadata fields,
#   * GS (0x1d) terminates the metadata block, before the ``--name-only`` file list.
_RS = "\x1e"
_US = "\x1f"
_GS = "\x1d"

#: ``git log --pretty=format:`` string emitting ``RS H US an US ae US aI US s US b GS``.
#: ``%aI`` is the author date in strict ISO-8601. The trailing ``%x1d`` (GS) lets the
#: parser split the metadata from the ``--name-only`` file lines that git appends after.
GIT_LOG_FORMAT = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1d"

#: Number of ``%x1f``-separated metadata fields in :data:`GIT_LOG_FORMAT`.
_FIELD_COUNT = 6


class GitSeedError(RuntimeError):
    """Raised when ``git`` cannot be run or reports a hard error (e.g. not a repo)."""


@dataclass(frozen=True, slots=True)
class GitCommit:
    """One commit's normalized metadata (the unit :func:`compose_content` renders).

    ``files`` is the set of paths touched by the commit (from ``git log --name-only``);
    it is empty for commits git reports no file list for (e.g. merges), which is fine —
    the episode simply omits the ``Files:`` section.
    """

    sha: str
    author_name: str
    author_email: str
    iso_date: str
    subject: str
    body: str = ""
    files: tuple[str, ...] = field(default_factory=tuple)


def git_log_args(max_count: int) -> list[str]:
    """Return the ``git log`` argv (sans the leading ``git``/``-C``) for ``max_count``.

    Kept pure/separate so tests can assert the exact flags without spawning git.
    """
    return [
        "log",
        f"--max-count={int(max_count)}",
        "--name-only",
        "--no-color",
        f"--pretty=format:{GIT_LOG_FORMAT}",
    ]


def parse_commit_chunk(chunk: str) -> GitCommit | None:
    """Parse one ``RS``-delimited commit chunk into a :class:`GitCommit`, or ``None``.

    A chunk is ``<metadata>GS<file-lines>`` where ``<metadata>`` is the six
    ``US``-separated fields. Returns ``None`` for an empty/whitespace-only chunk or one
    that does not carry the full field set (a defensive guard against malformed output).
    """
    if not chunk.strip():
        return None
    metadata, _, files_block = chunk.partition(_GS)
    fields = metadata.split(_US)
    if len(fields) < _FIELD_COUNT:
        return None
    sha, author_name, author_email, iso_date, subject, body = fields[:_FIELD_COUNT]
    files = tuple(line.strip() for line in files_block.splitlines() if line.strip())
    return GitCommit(
        sha=sha.strip(),
        author_name=author_name,
        author_email=author_email,
        iso_date=iso_date,
        subject=subject,
        body=body.strip(),
        files=files,
    )


def parse_git_log(raw: str) -> list[GitCommit]:
    """Parse the full :func:`git_log_args` output into commits (pure, in-memory).

    Splits on the record separator and delegates each chunk to
    :func:`parse_commit_chunk`. :func:`stream_git_log` is the streaming counterpart used
    by the CLI; this eager form keeps unit tests simple.
    """
    commits: list[GitCommit] = []
    for chunk in raw.split(_RS):
        commit = parse_commit_chunk(chunk)
        if commit is not None:
            commits.append(commit)
    return commits


def compose_content(commit: GitCommit) -> str:
    """Render one commit into the episode content string (no diffs, no network data).

    Layout is a compact, human/agent-readable summary::

        commit <sha>
        Author: <name> <email>
        Date: <iso date>

        <subject>

        <body>          # only when present

        Files:          # only when present
          <path>
          ...
    """
    lines: list[str] = [
        f"commit {commit.sha}",
        f"Author: {commit.author_name} <{commit.author_email}>",
        f"Date: {commit.iso_date}",
        "",
        commit.subject,
    ]
    if commit.body:
        lines.extend(("", commit.body))
    if commit.files:
        lines.append("")
        lines.append("Files:")
        lines.extend(f"  {path}" for path in commit.files)
    return "\n".join(lines)


def build_record(
    commit: GitCommit,
    *,
    namespace: str,
    repo: str | None,
    source: str = SEED_SOURCE,
) -> dict[str, Any]:
    """Build the spool episode dict for ``commit`` (pure; stable idempotency key).

    The key is derived from ``(session_id="git-seed:<namespace>", event_id=<full sha>,
    content)`` — all stable per commit — so re-seeding yields the identical key and the
    spool dedups it. ``EpisodeRecord`` is lazy-imported (session B owns it; consumed,
    never edited).
    """
    from memrelay.ingest.episode import EpisodeRecord

    return EpisodeRecord.new(
        compose_content(commit),
        namespace,
        repo=repo,
        source=source,
        session_id=f"git-seed:{namespace}",
        event_id=commit.sha,
    ).to_dict()


def iter_parse(text_chunks: Iterable[str]) -> Iterator[GitCommit]:
    """Yield commits from an iterable of raw text chunks, splitting on ``RS`` lazily.

    This is the pure streaming core shared by :func:`stream_git_log`: it buffers across
    chunk boundaries and only emits a :class:`GitCommit` once a full record separator has
    been seen, so no more than one commit's text is held at a time. Testable with any
    iterable of strings (no subprocess required).
    """
    buffer = ""
    for piece in text_chunks:
        buffer += piece
        # Everything before the last RS is one-or-more complete commits; the tail after
        # the last RS is a possibly-incomplete commit we keep buffering.
        parts = buffer.split(_RS)
        buffer = parts.pop()
        for part in parts:
            commit = parse_commit_chunk(part)
            if commit is not None:
                yield commit
    commit = parse_commit_chunk(buffer)
    if commit is not None:
        yield commit


def stream_git_log(
    path: str | Path,
    max_count: int = DEFAULT_MAX_COUNT,
    *,
    popen: Any = None,
) -> Iterator[GitCommit]:
    """Run ``git log`` at ``path`` and stream up to ``max_count`` commits (impure).

    The one I/O function: it spawns ``git`` and reads stdout incrementally, yielding a
    :class:`GitCommit` as each commit completes — the whole history is never buffered.
    ``popen`` is injectable (defaults to :class:`subprocess.Popen`) so tests can drive a
    fake process. A missing repo / non-zero git exit raises :class:`GitSeedError`.

    Note: as a generator, the ``git``-invocation and error check run only once iteration
    starts; callers that need eager validation should begin consuming it.
    """
    factory = popen if popen is not None else subprocess.Popen
    argv = ["git", "-C", str(path), *git_log_args(max_count)]
    try:
        proc = factory(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:  # git binary absent, path unusable, etc.
        raise GitSeedError(f"could not run git at {path!r}: {exc}") from exc

    stdout = proc.stdout
    if stdout is None:  # pragma: no cover - PIPE always yields a stream
        raise GitSeedError("git produced no output stream")

    def _read_chunks() -> Iterator[str]:
        # Iterating the text stream yields line-by-line, which streams without buffering
        # the whole log; ``iter_parse`` reassembles commits across those lines.
        yield from stdout

    yield from iter_parse(_read_chunks())

    return_code = proc.wait()
    if return_code != 0:
        stderr = proc.stderr.read() if proc.stderr is not None else ""
        detail = stderr.strip() or f"exit code {return_code}"
        raise GitSeedError(f"git log failed at {path!r}: {detail}")
