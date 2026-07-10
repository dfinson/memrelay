"""Hermetic end-to-end test for ``memrelay seed`` over a REAL git repo + REAL spool (#61).

Builds a throwaway git repository inside ``tmp_path`` (isolated git identity + fixed
dates, no network, no reliance on this project's own history), seeds it through the real
CLI into a real :class:`~memrelay.ingest.spool.Spool`, and proves the two load-bearing
ACs directly against the SQLite spool:

* every commit becomes exactly one episode with ``source="git"`` provenance, and
* **re-seeding is idempotent** — a second ``seed`` run adds ZERO net new rows (the spool's
  ``UNIQUE(idempotency_key)`` catches the stable per-commit keys).

Config and home are pinned to ``tmp_path`` so a real ``~/.memrelay`` is never touched; the
spool it exercises is genuinely real.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from click.testing import CliRunner

from memrelay import cli
from memrelay.config import Config
from memrelay.ingest.episode import from_row

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Seed Tester",
    "GIT_AUTHOR_EMAIL": "seed@example.com",
    "GIT_COMMITTER_NAME": "Seed Tester",
    "GIT_COMMITTER_EMAIL": "seed@example.com",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+00:00",
}


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Seed Tester",
            "-c",
            "user.email=seed@example.com",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            "-c",
            "init.defaultBranch=main",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        text=True,
        env={**_GIT_ENV, "PATH": _path_env()},
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")


def _path_env() -> str:
    import os

    return os.environ.get("PATH", "")


def _build_repo(repo: Path) -> list[str]:
    """Create ``repo`` with three commits; return their SHAs (newest first)."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    for index in range(3):
        (repo / f"file{index}.txt").write_text(f"content {index}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", f"Commit number {index}\n\nBody for commit {index}.")
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "--pretty=%H"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


def _episode_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()
    finally:
        conn.close()
    return int(count)


def _episode_records(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT record FROM episodes ORDER BY seq").fetchall()
    finally:
        conn.close()
    return [from_row(row[0]) for row in rows]


def _run_seed(monkeypatch, tmp_path: Path, repo: Path, db_path: Path) -> None:
    # Pin config + home to tmp so no real ~/.memrelay is read or created; the spool the
    # command opens (via --spool) is a genuine on-disk Spool.
    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    result = CliRunner().invoke(
        cli.main,
        ["seed", "--path", str(repo), "--spool", str(db_path), "--namespace", "testns"],
    )
    assert result.exit_code == 0, result.output


def test_seed_ingests_git_history_into_real_spool(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shas = _build_repo(repo)
    db_path = tmp_path / "spool" / "spool.db"

    _run_seed(monkeypatch, tmp_path, repo, db_path)

    assert _episode_count(db_path) == len(shas) == 3
    records = _episode_records(db_path)
    assert all(rec["source"] == "git" for rec in records)
    assert all(rec["namespace"] == "testns" for rec in records)
    assert all(rec["session_id"] == "git-seed:testns" for rec in records)
    # Every episode's event_id is a real commit sha, and content carries the touched file.
    assert {rec["event_id"] for rec in records} == set(shas)
    assert any("file0.txt" in rec["content"] for rec in records)
    # No diffs leaked into the seeded content.
    assert all("diff --git" not in rec["content"] for rec in records)


def test_reseed_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _build_repo(repo)
    db_path = tmp_path / "spool" / "spool.db"

    _run_seed(monkeypatch, tmp_path, repo, db_path)
    first = _episode_count(db_path)
    keys_after_first = {rec["idempotency_key"] for rec in _episode_records(db_path)}

    _run_seed(monkeypatch, tmp_path, repo, db_path)
    second = _episode_count(db_path)
    keys_after_second = {rec["idempotency_key"] for rec in _episode_records(db_path)}

    assert first == 3
    # The crux AC: a second seed adds zero net new rows.
    assert second == first
    assert keys_after_second == keys_after_first


def test_seed_respects_max_count(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _build_repo(repo)
    db_path = tmp_path / "spool" / "spool.db"

    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    result = CliRunner().invoke(
        cli.main,
        [
            "seed",
            "--path",
            str(repo),
            "--spool",
            str(db_path),
            "--namespace",
            "testns",
            "--max-count",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _episode_count(db_path) == 2


def test_seed_dry_run_writes_no_spool(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _build_repo(repo)
    db_path = tmp_path / "spool" / "spool.db"

    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    result = CliRunner().invoke(
        cli.main,
        ["seed", "--path", str(repo), "--spool", str(db_path), "--namespace", "ns", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "dry run: would seed" in result.output
    assert not db_path.exists()  # nothing opened the spool
