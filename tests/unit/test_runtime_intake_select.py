"""Unit tests: the daemon poller selects its per-session capture by ``intake_source`` (#11).

``default_poller_factory`` always builds the real :class:`SessionDiscoveryPoller`, but which
capture it runs per session is gated on ``config.ingest.intake_source``:

* ``"replay"`` (the default, RULING 2) → #8's unchanged :class:`RunObserveCapture`, so the
  shipping default is byte-identical to today (file_watch is opt-in until it soaks).
* ``"file_watch"`` → the live :class:`LiveTailCapture` (the retained replay backstop + a
  real-time FileWatch tail).

We assert the selection by *building* a poller (hermetic — merely opens the shared spool, no
polling, no discovery) and calling its injected ``capture_factory`` on a ref, checking the
concrete capture type. Construction is not ``start`` — no task, observer, or tail is launched —
so this stays engine-free and leak-free.
"""

from __future__ import annotations

from pathlib import Path

from memrelay.config import load_config
from memrelay.daemon.runtime import default_poller_factory
from memrelay.daemon.session_discovery import LiveTailCapture, RunObserveCapture
from memrelay.providers.base import SessionRef


def _poller(tmp_path: Path, intake_source: str | None):
    ingest = {"intake_source": intake_source} if intake_source is not None else None
    cfg = load_config(environ={}, home=str(tmp_path), ingest=ingest)
    return default_poller_factory(object(), cfg)


def _ref() -> SessionRef:
    # The path is never touched by capture *construction* (only by start/run), so a dummy
    # path keeps this hermetic.
    return SessionRef(session_id="s-1", agent_id="copilot", path="C:/none/events.jsonl")


def test_default_intake_selects_run_observe_capture(tmp_path: Path) -> None:
    poller = _poller(tmp_path, None)  # unset → default "replay"
    assert poller is not None
    capture = poller._capture_factory(_ref())
    assert isinstance(capture, RunObserveCapture)
    assert not isinstance(capture, LiveTailCapture)


def test_replay_intake_selects_run_observe_capture(tmp_path: Path) -> None:
    poller = _poller(tmp_path, "replay")
    assert poller is not None
    assert isinstance(poller._capture_factory(_ref()), RunObserveCapture)


def test_file_watch_intake_selects_live_tail_capture(tmp_path: Path) -> None:
    poller = _poller(tmp_path, "file_watch")
    assert poller is not None
    capture = poller._capture_factory(_ref())
    assert isinstance(capture, LiveTailCapture)
