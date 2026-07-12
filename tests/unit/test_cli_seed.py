"""Unit tests for the ``memrelay seed`` CLI command (git + spool mocked out).

The command wiring is exercised in isolation: ``git_seed.stream_git_log`` and the spool
opener are patched so the test asserts CLI plumbing (namespace resolution, argument
routing, dry-run, error handling, output) without a real git repo or a real spool.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from memrelay import cli
from memrelay.config import Config, Namespace, NamespacesConfig
from memrelay.ingest import git_seed


class _RecordingSpool:
    def __init__(self) -> None:
        self.appended: list[dict] = []
        self.closed = False

    def append(self, record: dict) -> None:
        self.appended.append(record)

    def close(self) -> None:
        self.closed = True


def _commit(sha: str) -> git_seed.GitCommit:
    return git_seed.GitCommit(
        sha=sha,
        author_name="Dev",
        author_email="dev@example.com",
        iso_date="2020-01-01T00:00:00+00:00",
        subject=f"subject {sha}",
        body="",
        files=(f"{sha}.py",),
    )


def test_seed_appends_one_record_per_commit(monkeypatch, tmp_path: Path) -> None:
    spool = _RecordingSpool()
    captured: dict = {}

    def fake_stream(path, max_count, **kwargs):
        captured["path"] = path
        captured["max_count"] = max_count
        return iter([_commit("aaa"), _commit("bbb")])

    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr(cli, "_open_spool", lambda db_path: spool)
    monkeypatch.setattr(git_seed, "stream_git_log", fake_stream)
    monkeypatch.setattr("memrelay.mcp.namespace.current_repo", lambda cwd=None: "acme/widgets")

    result = CliRunner().invoke(cli.main, ["seed", "--path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert len(spool.appended) == 2
    assert spool.closed is True
    assert {r["event_id"] for r in spool.appended} == {"aaa", "bbb"}
    assert all(r["source"] == "git" for r in spool.appended)
    assert "commits:   2" in result.output
    assert "repo:      acme/widgets" in result.output
    assert "namespace: acme" in result.output


def test_seed_namespace_override_wins(monkeypatch, tmp_path: Path) -> None:
    spool = _RecordingSpool()
    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr(cli, "_open_spool", lambda db_path: spool)
    monkeypatch.setattr(git_seed, "stream_git_log", lambda p, n, **k: iter([_commit("x")]))
    monkeypatch.setattr("memrelay.mcp.namespace.current_repo", lambda cwd=None: "acme/widgets")

    result = CliRunner().invoke(cli.main, ["seed", "--namespace", "chosen-ns"])

    assert result.exit_code == 0, result.output
    assert "namespace: chosen-ns" in result.output
    assert spool.appended[0]["namespace"] == "chosen-ns"


def test_seed_repo_override_sets_provenance(monkeypatch, tmp_path: Path) -> None:
    spool = _RecordingSpool()
    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr(cli, "_open_spool", lambda db_path: spool)
    monkeypatch.setattr(git_seed, "stream_git_log", lambda p, n, **k: iter([_commit("x")]))

    result = CliRunner().invoke(cli.main, ["seed", "--repo", "OWNER/Name"])

    assert result.exit_code == 0, result.output
    # repo overrides provenance; namespace derives from the override's owner.
    assert spool.appended[0]["repo"] == "OWNER/Name"
    assert "namespace: owner" in result.output


def test_seed_dry_run_appends_nothing(monkeypatch, tmp_path: Path) -> None:
    opened: dict = {"count": 0}

    def fake_open_spool(db_path):
        opened["count"] += 1
        return _RecordingSpool()

    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr(cli, "_open_spool", fake_open_spool)
    monkeypatch.setattr(
        git_seed, "stream_git_log", lambda p, n, **k: iter([_commit("a"), _commit("b")])
    )
    monkeypatch.setattr("memrelay.mcp.namespace.current_repo", lambda cwd=None: "o/r")

    result = CliRunner().invoke(cli.main, ["seed", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert opened["count"] == 0  # spool never opened on a dry run
    assert "dry run: would seed" in result.output
    assert "commits:   2" in result.output


def test_seed_uses_explicit_spool_path(monkeypatch, tmp_path: Path) -> None:
    opened: dict = {}

    def fake_open_spool(db_path):
        opened["db_path"] = Path(db_path)
        return _RecordingSpool()

    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr(cli, "_open_spool", fake_open_spool)
    monkeypatch.setattr(git_seed, "stream_git_log", lambda p, n, **k: iter([_commit("a")]))
    monkeypatch.setattr("memrelay.mcp.namespace.current_repo", lambda cwd=None: "o/r")

    spool_arg = tmp_path / "custom" / "spool.db"
    result = CliRunner().invoke(cli.main, ["seed", "--spool", str(spool_arg)])

    assert result.exit_code == 0, result.output
    assert opened["db_path"] == spool_arg


def test_seed_threads_config_repo_map_into_namespace(monkeypatch, tmp_path: Path) -> None:
    """A ``[namespaces.*]`` entry maps the repo to its shared namespace (mirrors observe)."""
    spool = _RecordingSpool()
    cfg = Config(namespaces=NamespacesConfig((Namespace("acme", ("acme/api",)),)))
    monkeypatch.setattr(cli, "_load_config", lambda: cfg)
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr(cli, "_open_spool", lambda db_path: spool)
    monkeypatch.setattr(git_seed, "stream_git_log", lambda p, n, **k: iter([_commit("a")]))
    monkeypatch.setattr("memrelay.mcp.namespace.current_repo", lambda cwd=None: "acme/api")

    result = CliRunner().invoke(cli.main, ["seed"])

    assert result.exit_code == 0, result.output
    assert "namespace: acme" in result.output
    assert spool.appended[0]["namespace"] == "acme"


def test_seed_passes_max_count_through(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_stream(path, max_count, **kwargs):
        captured["max_count"] = max_count
        return iter([])

    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr(cli, "_open_spool", lambda db_path: _RecordingSpool())
    monkeypatch.setattr(git_seed, "stream_git_log", fake_stream)
    monkeypatch.setattr("memrelay.mcp.namespace.current_repo", lambda cwd=None: "o/r")

    result = CliRunner().invoke(cli.main, ["seed", "--max-count", "7"])

    assert result.exit_code == 0, result.output
    assert captured["max_count"] == 7


def test_seed_default_max_count(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_stream(path, max_count, **kwargs):
        captured["max_count"] = max_count
        return iter([])

    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr(cli, "_open_spool", lambda db_path: _RecordingSpool())
    monkeypatch.setattr(git_seed, "stream_git_log", fake_stream)
    monkeypatch.setattr("memrelay.mcp.namespace.current_repo", lambda cwd=None: "o/r")

    result = CliRunner().invoke(cli.main, ["seed"])

    assert result.exit_code == 0, result.output
    assert captured["max_count"] == git_seed.DEFAULT_MAX_COUNT


def test_seed_reports_git_error_cleanly(monkeypatch, tmp_path: Path) -> None:
    spool = _RecordingSpool()

    def boom(path, max_count, **kwargs):
        raise git_seed.GitSeedError("git log failed at '/x': fatal: not a git repository")
        yield  # pragma: no cover - never reached; makes this a generator

    monkeypatch.setattr(cli, "_load_config", lambda: Config())
    monkeypatch.setattr(cli, "ensure_home", lambda cfg: tmp_path)
    monkeypatch.setattr(cli, "_open_spool", lambda db_path: spool)
    monkeypatch.setattr(git_seed, "stream_git_log", boom)
    monkeypatch.setattr("memrelay.mcp.namespace.current_repo", lambda cwd=None: "o/r")

    result = CliRunner().invoke(cli.main, ["seed"])

    assert result.exit_code != 0
    assert "not a git repository" in result.output
    assert spool.closed is True  # opened spool is still closed on the error path
