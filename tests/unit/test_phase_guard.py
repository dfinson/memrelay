"""Unit tests for the opt-in phase preflight guard (E2-S6 #98).

:func:`resolve_phase` is the single fail-safe that keeps an opt-in-but-broken phase
config from taking down the ingest pipeline. Both traceforge failure modes (a missing
model bundle that would crash ``new_stream``, and a missing ML dep that would raise in
the trial embed) must collapse to the same explicit, logged ``(False, None)`` degrade —
never a raise. The factory/trial are injectable so every branch is covered here without
ever touching the real ~37KB model bundle, keeping the unit suite offline.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from memrelay.ingest.phase_guard import resolve_phase


def _cfg(enable_phase: bool) -> SimpleNamespace:
    """Minimal duck type for the ``cfg.ingest.enable_phase`` the guard reads."""
    return SimpleNamespace(ingest=SimpleNamespace(enable_phase=enable_phase))


class _Inferencer:
    """Opaque stand-in for ``traceforge.phase.inferencer.PhaseInferencer``."""


def test_phase_off_returns_false_and_touches_nothing() -> None:
    calls: list[str] = []
    result = resolve_phase(
        _cfg(False),
        factory=lambda: (calls.append("made"), _Inferencer())[1],
        trial=lambda inf: calls.append("trial"),
    )
    assert result == (False, None)
    assert calls == [], "phase-off must not construct or preflight the model"


def test_enabled_override_beats_config_false() -> None:
    made = _Inferencer()
    active, inferencer = resolve_phase(
        _cfg(False), enabled=True, factory=lambda: made, trial=lambda inf: None
    )
    assert active is True
    assert inferencer is made


def test_disabled_override_beats_config_true() -> None:
    calls: list[str] = []
    result = resolve_phase(
        _cfg(True),
        enabled=False,
        factory=lambda: calls.append("made") or _Inferencer(),
        trial=lambda inf: calls.append("trial"),
    )
    assert result == (False, None)
    assert calls == [], "an explicit enabled=False override must short-circuit"


def test_success_returns_warm_inferencer_after_trial() -> None:
    made = _Inferencer()
    trials: list[Any] = []
    active, inferencer = resolve_phase(_cfg(True), factory=lambda: made, trial=trials.append)
    assert active is True
    assert inferencer is made
    assert trials == [made], "the preflight trial must run against the constructed inferencer"


def test_missing_bundle_degrades_without_raising() -> None:
    # Mirrors the ``new_stream`` crash path: the trial (model load) raises FileNotFoundError.
    def boom_trial(inferencer: Any) -> None:
        raise FileNotFoundError("phase-model.joblib missing")

    result = resolve_phase(_cfg(True), factory=_Inferencer, trial=boom_trial)
    assert result == (False, None)


def test_missing_deps_degrades_without_raising() -> None:
    # Mirrors the silent-unstamped path: model2vec / its transitive ``requests`` import fails.
    def boom_trial(inferencer: Any) -> None:
        raise ModuleNotFoundError("No module named 'requests'")

    result = resolve_phase(_cfg(True), factory=_Inferencer, trial=boom_trial)
    assert result == (False, None)


def test_factory_failure_degrades_without_raising() -> None:
    def boom_factory() -> Any:
        raise RuntimeError("cannot construct inferencer")

    result = resolve_phase(_cfg(True), factory=boom_factory, trial=lambda inf: None)
    assert result == (False, None)
