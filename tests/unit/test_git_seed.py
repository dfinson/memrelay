"""Unit tests for the pure ``git_seed`` core (E9-S4 #61).

Parsing, composition, and record-building are exercised against literal ``git log``
text — no real repo, no subprocess — so the interesting logic is fully deterministic.
The one impure function, :func:`git_seed.stream_git_log`, is driven with a fake Popen.
"""

from __future__ import annotations

from memrelay.ingest import git_seed
from memrelay.ingest.episode import EPISODE_FIELDS, make_idempotency_key

_RS = "\x1e"
_US = "\x1f"
_GS = "\x1d"


def _chunk(sha, name, email, date, subject, body, files):
    """Build one raw ``git log`` commit block exactly as :data:`GIT_LOG_FORMAT` emits it."""
    metadata = _US.join((sha, name, email, date, subject, body))
    file_lines = "".join(f"\n{path}" for path in files)
    return f"{_RS}{metadata}{_GS}{file_lines}\n"


def test_parse_git_log_reads_multiple_commits() -> None:
    raw = _chunk(
        "aaa111",
        "Ada Lovelace",
        "ada@example.com",
        "2020-01-02T03:04:05+00:00",
        "First commit",
        "",
        ["a.py", "b.py"],
    ) + _chunk(
        "bbb222",
        "Alan Turing",
        "alan@example.com",
        "2020-02-03T04:05:06+00:00",
        "Second commit",
        "",
        ["c.py"],
    )

    commits = git_seed.parse_git_log(raw)

    assert [c.sha for c in commits] == ["aaa111", "bbb222"]
    assert commits[0].author_name == "Ada Lovelace"
    assert commits[0].author_email == "ada@example.com"
    assert commits[0].iso_date == "2020-01-02T03:04:05+00:00"
    assert commits[0].subject == "First commit"
    assert commits[0].files == ("a.py", "b.py")
    assert commits[1].files == ("c.py",)


def test_parse_git_log_preserves_multiline_body() -> None:
    body = "Line one of body\nLine two of body"
    raw = _chunk(
        "ccc333",
        "Grace Hopper",
        "grace@example.com",
        "2021-05-06T07:08:09+00:00",
        "Fix the compiler",
        body,
        ["compiler.py"],
    )

    (commit,) = git_seed.parse_git_log(raw)

    assert commit.body == body
    assert commit.files == ("compiler.py",)


def test_parse_git_log_handles_commit_without_files() -> None:
    """A merge (or otherwise file-less) commit parses with an empty ``files`` tuple."""
    raw = _chunk(
        "ddd444",
        "Merger",
        "merge@example.com",
        "2022-01-01T00:00:00+00:00",
        "Merge branch 'feature'",
        "",
        [],
    )

    (commit,) = git_seed.parse_git_log(raw)

    assert commit.subject == "Merge branch 'feature'"
    assert commit.files == ()


def test_parse_git_log_ignores_blank_and_malformed_chunks() -> None:
    good = _chunk("eee555", "Dev", "dev@example.com", "2023-01-01T00:00:00+00:00", "ok", "", [])
    # A stray chunk missing the field separators must be skipped, not crash.
    malformed = f"{_RS}not-a-real-metadata-block{_GS}\n"

    commits = git_seed.parse_git_log("   " + malformed + good)

    assert [c.sha for c in commits] == ["eee555"]


def test_compose_content_includes_metadata_files_and_body() -> None:
    commit = git_seed.GitCommit(
        sha="abc123",
        author_name="Ada Lovelace",
        author_email="ada@example.com",
        iso_date="2020-01-02T03:04:05+00:00",
        subject="Add the widget",
        body="A longer explanation\nspanning two lines.",
        files=("src/widget.py", "tests/test_widget.py"),
    )

    content = git_seed.compose_content(commit)

    assert "commit abc123" in content
    assert "Author: Ada Lovelace <ada@example.com>" in content
    assert "Date: 2020-01-02T03:04:05+00:00" in content
    assert "Add the widget" in content
    assert "A longer explanation\nspanning two lines." in content
    assert "Files:" in content
    assert "  src/widget.py" in content
    assert "  tests/test_widget.py" in content
    # No diffs are ever included.
    assert "diff --git" not in content
    assert "@@" not in content


def test_compose_content_omits_empty_sections() -> None:
    commit = git_seed.GitCommit(
        sha="def456",
        author_name="Solo",
        author_email="solo@example.com",
        iso_date="2020-01-01T00:00:00+00:00",
        subject="Subject only",
        body="",
        files=(),
    )

    content = git_seed.compose_content(commit)

    assert "Subject only" in content
    assert "Files:" not in content


def test_build_record_sets_provenance_and_identity() -> None:
    commit = git_seed.GitCommit(
        sha="feedface",
        author_name="Dev",
        author_email="dev@example.com",
        iso_date="2020-01-01T00:00:00+00:00",
        subject="Seed me",
        body="",
        files=("f.py",),
    )

    record = git_seed.build_record(commit, namespace="acme", repo="acme/widgets")

    # Serializes to exactly the frozen episode wire shape.
    assert set(record) == set(EPISODE_FIELDS)
    assert record["namespace"] == "acme"
    assert record["repo"] == "acme/widgets"
    assert record["source"] == "git"
    assert record["session_id"] == "git-seed:acme"
    assert record["event_id"] == "feedface"


def test_build_record_key_is_stable_across_runs() -> None:
    """The idempotency crux: the same commit yields the same key every time (#61)."""
    commit = git_seed.GitCommit(
        sha="c0ffee",
        author_name="Dev",
        author_email="dev@example.com",
        iso_date="2020-01-01T00:00:00+00:00",
        subject="Stable",
        body="body",
        files=("a.py",),
    )

    first = git_seed.build_record(commit, namespace="ns", repo="o/r")
    second = git_seed.build_record(commit, namespace="ns", repo="o/r")

    assert first["idempotency_key"] == second["idempotency_key"]
    # And it is exactly the key the spool derives from (session_id, event_id, content).
    assert first["idempotency_key"] == make_idempotency_key(
        "git-seed:ns", "c0ffee", git_seed.compose_content(commit)
    )


def test_build_record_key_differs_per_commit() -> None:
    base = {
        "author_name": "Dev",
        "author_email": "dev@example.com",
        "iso_date": "2020-01-01T00:00:00+00:00",
        "subject": "s",
        "body": "",
        "files": (),
    }
    one = git_seed.build_record(git_seed.GitCommit(sha="1111", **base), namespace="ns", repo="o/r")
    two = git_seed.build_record(git_seed.GitCommit(sha="2222", **base), namespace="ns", repo="o/r")

    assert one["idempotency_key"] != two["idempotency_key"]


def test_git_log_args_bounds_and_formats() -> None:
    args = git_seed.git_log_args(42)

    assert "log" in args
    assert "--max-count=42" in args
    assert "--name-only" in args
    assert f"--pretty=format:{git_seed.GIT_LOG_FORMAT}" in args


def test_iter_parse_streams_across_arbitrary_chunk_boundaries() -> None:
    """Commits are yielded even when the raw text is fed in mid-record slices."""
    raw = _chunk("s1", "A", "a@x", "2020-01-01T00:00:00+00:00", "one", "", ["a"]) + _chunk(
        "s2", "B", "b@x", "2020-01-02T00:00:00+00:00", "two", "", ["b"]
    )
    # Feed one character at a time to stress the buffering.
    commits = list(git_seed.iter_parse(iter(raw)))

    assert [c.sha for c in commits] == ["s1", "s2"]


class _FakeProc:
    def __init__(self, stdout_text: str, returncode: int = 0, stderr_text: str = "") -> None:
        self.stdout = iter(stdout_text.splitlines(keepends=True))
        self.stderr = _FakeStream(stderr_text)
        self._returncode = returncode
        self.returncode: int | None = None

    def wait(self) -> int:
        self.returncode = self._returncode
        return self._returncode


class _FakeStream:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


def test_stream_git_log_yields_commits_via_injected_popen() -> None:
    raw = _chunk("abc", "Dev", "dev@x", "2020-01-01T00:00:00+00:00", "subject", "", ["f.py"])
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return _FakeProc(raw)

    commits = list(git_seed.stream_git_log("/repo", 10, popen=fake_popen))

    assert [c.sha for c in commits] == ["abc"]
    assert captured["argv"][:3] == ["git", "-C", "/repo"]
    assert "--max-count=10" in captured["argv"]


def test_stream_git_log_raises_on_nonzero_exit() -> None:
    def fake_popen(argv, **kwargs):
        return _FakeProc("", returncode=128, stderr_text="fatal: not a git repository")

    try:
        list(git_seed.stream_git_log("/nope", 10, popen=fake_popen))
    except git_seed.GitSeedError as exc:
        assert "not a git repository" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected GitSeedError")


def test_stream_git_log_raises_when_git_missing() -> None:
    def fake_popen(argv, **kwargs):
        raise OSError("git not found")

    try:
        list(git_seed.stream_git_log("/repo", 10, popen=fake_popen))
    except git_seed.GitSeedError as exc:
        assert "could not run git" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected GitSeedError")
