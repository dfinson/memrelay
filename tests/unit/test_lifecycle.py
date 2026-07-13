"""Deterministic unit tests for daemon start-UX + startup-log capture.

Covers two first-run daemon bugs fixed in :mod:`memrelay.daemon.lifecycle` with **no
wall-clock dependence**:

* **Wall C** -- ``memrelay start`` reported a false failure because the old 10 s
  readiness wait was shorter than a cold first-run engine build. The wait is now raised,
  adapts to a cold-vs-warm start, and is env-configurable; a slow-but-eventually-healthy
  start no longer raises; the timeout message is honest and points at ``memrelay status``.
* **Wall D** -- a detached daemon that died during startup left no trace because its
  stdout/stderr went to ``DEVNULL``. ``spawn_detached`` now captures the child's
  stdout+stderr to ``<home>/logs/daemon-startup.log`` while keeping it fully detached.

The daemon is never really launched for the Wall C tests (``spawn_detached`` /
``probe_health`` are the module seams the tests monkeypatch). The Wall D capture test
launches a tiny echoing child via the ``_serve_argv`` seam and joins it deterministically
(``Popen.wait``) rather than sleeping.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from memrelay.config import Config, GraphConfig
from memrelay.daemon import lifecycle


def _config(tmp_path: Path) -> Config:
    """A hermetic config whose home + graph path live under ``tmp_path``.

    The graph file does not exist yet, so :func:`lifecycle._is_cold_start` reads the
    config as a cold first run until a test creates it.
    """
    return Config(
        home=str(tmp_path / "mem"),
        graph=GraphConfig(path=str(tmp_path / "graph.db")),
    )


# ─── Wall C: readiness timeout is raised / adaptive / configurable ────────────


def test_ready_timeout_is_raised_above_legacy_10s() -> None:
    """The readiness window is no longer the false-failure-inducing 10 s."""
    assert lifecycle.READY_TIMEOUT > 10.0
    assert lifecycle.COLD_READY_TIMEOUT >= lifecycle.READY_TIMEOUT


def test_resolve_ready_timeout_is_cold_before_first_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first-ever start (no graph db yet) gets the wide cold window."""
    monkeypatch.delenv(lifecycle.READY_TIMEOUT_ENV, raising=False)
    cfg = _config(tmp_path)
    assert not cfg.graph_path.exists()
    assert lifecycle._resolve_ready_timeout(cfg) == lifecycle.COLD_READY_TIMEOUT


def test_resolve_ready_timeout_is_warm_once_graph_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the engine has built (graph db present), a restart uses the warm window."""
    monkeypatch.delenv(lifecycle.READY_TIMEOUT_ENV, raising=False)
    cfg = _config(tmp_path)
    cfg.graph_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.graph_path.write_text("", encoding="utf-8")  # a prior build left the graph
    assert lifecycle._resolve_ready_timeout(cfg) == lifecycle.READY_TIMEOUT


def test_resolve_ready_timeout_env_override_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A positive ``MEMRELAY_READY_TIMEOUT`` overrides the adaptive default."""
    cfg = _config(tmp_path)  # cold
    monkeypatch.setenv(lifecycle.READY_TIMEOUT_ENV, "3.5")
    assert lifecycle._resolve_ready_timeout(cfg) == 3.5


def test_resolve_ready_timeout_ignores_bad_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-numeric or non-positive override is ignored (falls back to adaptive)."""
    cfg = _config(tmp_path)  # cold
    monkeypatch.setenv(lifecycle.READY_TIMEOUT_ENV, "not-a-number")
    assert lifecycle._resolve_ready_timeout(cfg) == lifecycle.COLD_READY_TIMEOUT
    monkeypatch.setenv(lifecycle.READY_TIMEOUT_ENV, "0")
    assert lifecycle._resolve_ready_timeout(cfg) == lifecycle.COLD_READY_TIMEOUT


def test_slow_but_eventually_healthy_start_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A daemon that becomes healthy only after several probes must NOT raise.

    Health turns positive on the 4th probe (past the two pre-spawn checks and an early
    poll). Driven by probe *count*, not the clock, so the window value is irrelevant to
    the outcome.
    """
    cfg = _config(tmp_path)
    monkeypatch.setattr(lifecycle, "spawn_detached", lambda home: 4242)

    calls = {"n": 0}
    healthy = {"status": "ok"}

    def fake_probe(home: Path, timeout: float = lifecycle.PROBE_TIMEOUT) -> dict | None:
        calls["n"] += 1
        return healthy if calls["n"] >= 4 else None

    monkeypatch.setattr(lifecycle, "probe_health", fake_probe)

    status = lifecycle.start_daemon(cfg, ready_timeout=5.0, poll_interval=0.0)

    assert status.running is True
    assert status.pid == 4242
    assert status.health == healthy
    assert calls["n"] >= 4  # it really did keep polling past the unhealthy probes


def test_timeout_message_is_honest_and_names_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the window is exhausted the error is honest and points at ``memrelay status``."""
    cfg = _config(tmp_path)
    monkeypatch.setattr(lifecycle, "spawn_detached", lambda home: 4242)
    monkeypatch.setattr(
        lifecycle, "probe_health", lambda home, timeout=lifecycle.PROBE_TIMEOUT: None
    )

    with pytest.raises(lifecycle.DaemonStartError) as exc_info:
        lifecycle.start_daemon(cfg, ready_timeout=0.01, poll_interval=0.0)

    message = str(exc_info.value)
    assert "memrelay status" in message
    # The old misleading "did not become healthy" wording is gone.
    assert "did not become healthy" not in message


def test_start_daemon_resolves_timeout_when_none_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI path (``start_daemon(cfg)`` with no explicit timeout) resolves adaptively.

    This is the crux of Wall C: ``cli.py`` calls ``start_daemon(cfg)``, so the first-run
    benefit only lands if the *default* path runs :func:`_resolve_ready_timeout`.
    """
    cfg = _config(tmp_path)
    monkeypatch.setattr(lifecycle, "spawn_detached", lambda home: 4242)

    calls = {"n": 0}

    def fake_probe(home: Path, timeout: float = lifecycle.PROBE_TIMEOUT) -> dict | None:
        calls["n"] += 1
        # First (pre-lock) probe unhealthy so resolution is reached; then healthy.
        return None if calls["n"] == 1 else {"status": "ok"}

    monkeypatch.setattr(lifecycle, "probe_health", fake_probe)

    seen: list[Config] = []
    real_resolve = lifecycle._resolve_ready_timeout

    def spy_resolve(config: Config) -> float:
        seen.append(config)
        return real_resolve(config)

    monkeypatch.setattr(lifecycle, "_resolve_ready_timeout", spy_resolve)

    status = lifecycle.start_daemon(cfg)  # ready_timeout omitted -> adaptive path

    assert status.running is True
    assert seen == [cfg]


# ─── Wall D: detached child's stderr is captured to a startup log FILE ─────────


def test_spawn_detached_captures_child_stderr_to_startup_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child's stderr (and stdout) land in ``<home>/logs/daemon-startup.log``."""
    home = tmp_path / "home"
    home.mkdir()

    marker_err = "startup-boom-stderr"
    marker_out = "startup-hi-stdout"
    child = (
        "import sys; "
        f"sys.stdout.write({marker_out!r}); sys.stdout.flush(); "
        f"sys.stderr.write({marker_err!r}); sys.stderr.flush()"
    )
    monkeypatch.setattr(lifecycle, "_serve_argv", lambda: [sys.executable, "-c", child])

    # Capture the real Popen so the test can join the detached child deterministically
    # (no sleeps) before reading the log file.
    created: list[subprocess.Popen] = []
    real_popen = lifecycle.subprocess.Popen

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen:
        proc = real_popen(*args, **kwargs)
        created.append(proc)
        return proc

    monkeypatch.setattr(lifecycle.subprocess, "Popen", recording_popen)

    pid = lifecycle.spawn_detached(home)

    assert isinstance(pid, int)
    assert len(created) == 1
    created[0].wait(timeout=30)  # deterministic join on the child process

    log = lifecycle.startup_log_path(home)
    assert log.is_file()
    contents = log.read_text(encoding="utf-8")
    assert marker_err in contents  # stderr captured -- the Wall D deliverable
    assert marker_out in contents  # stdout merged into the same file


def test_spawn_detached_appends_across_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second spawn appends, so a crash's trace survives an immediate retry."""
    home = tmp_path / "home"
    home.mkdir()

    real_popen = lifecycle.subprocess.Popen
    joined: list[subprocess.Popen] = []

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen:
        proc = real_popen(*args, **kwargs)
        joined.append(proc)
        return proc

    monkeypatch.setattr(lifecycle.subprocess, "Popen", recording_popen)

    for marker in ("first-run-line", "second-run-line"):
        monkeypatch.setattr(
            lifecycle,
            "_serve_argv",
            lambda m=marker: [sys.executable, "-c", f"import sys; sys.stderr.write({m!r})"],
        )
        lifecycle.spawn_detached(home)
        joined[-1].wait(timeout=30)

    contents = lifecycle.startup_log_path(home).read_text(encoding="utf-8")
    assert "first-run-line" in contents
    assert "second-run-line" in contents  # append, not truncate


def test_spawn_detached_uses_a_file_not_a_pipe_and_stays_detached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The capture target is a real file (never a pipe) and detachment is preserved."""
    home = tmp_path / "home"
    home.mkdir()

    captured: dict = {}

    class FakePopen:
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            # Inspect the stream while the file is still open (before the caller's
            # ``with`` block closes the parent handle).
            stdout = kwargs["stdout"]
            captured["stdout_is_file"] = hasattr(stdout, "fileno")
            captured["stdout_name"] = getattr(stdout, "name", None)
            self.pid = 31337

    monkeypatch.setattr(lifecycle.subprocess, "Popen", FakePopen)

    pid = lifecycle.spawn_detached(home)
    assert pid == 31337

    kwargs = captured["kwargs"]
    # stdin is DEVNULL: the parent holds nothing the child could block on.
    assert kwargs["stdin"] == subprocess.DEVNULL
    # stdout is a real FILE at the startup-log path -- not a pipe / devnull.
    assert captured["stdout_is_file"]
    assert kwargs["stdout"] not in (subprocess.PIPE, subprocess.DEVNULL)
    assert Path(captured["stdout_name"]) == lifecycle.startup_log_path(home)
    # stderr merges into that same file (never a pipe).
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["stderr"] != subprocess.PIPE
    # The argv still runs the real serve command by default.
    assert captured["argv"] == lifecycle._serve_argv()

    # Detachment flags are preserved unchanged.
    if os.name == "nt":
        flags = kwargs["creationflags"]
        assert flags & subprocess.DETACHED_PROCESS
        assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert kwargs["start_new_session"] is True

    # The log directory was created under home.
    assert lifecycle.startup_log_path(home).parent.is_dir()
