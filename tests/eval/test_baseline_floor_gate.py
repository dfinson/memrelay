"""Hermetic tests for the absolute-floor logic of the precision@k gate (rt-release F1, #153).

These exercise ``generate_baseline._write`` / ``_check`` directly with a stubbed
``_build_report`` and a temp ``BASELINE_PATH`` — no engine build, no network — so they pin the
gate-hardening behavior fast and deterministically:

* ``--write`` refuses (exit 1, no file written) when a measured metric is below the hardcoded
  floor, so the committed baseline cannot be silently walked down; and
* ``--check`` fails when the *committed* baseline is itself below the floor, and its per-metric
  threshold is ``max(baseline - margin, floor)`` — a measurement that clears ``baseline - margin``
  but not the floor still fails (the floor genuinely tightens the gate).
"""

from __future__ import annotations

import json
from pathlib import Path

import generate_baseline
import pytest

# A healthy metric set at/above every floor (mirrors the shape of the real baseline).
_HEALTHY = {"p@1": 0.75, "p@3": 0.666667, "p@5": 0.40, "hit@3": 1.0, "hit@5": 1.0}


def _stub_report(monkeypatch: pytest.MonkeyPatch, metrics: dict) -> None:
    monkeypatch.setattr(
        generate_baseline,
        "_build_report",
        lambda: {"metrics": dict(metrics), "config": {"n_topics": 1}},
    )


def _write_baseline(path: Path, metrics: dict, *, margin: float = 0.05) -> None:
    artifact = {
        "margin": margin,
        "enforced": list(generate_baseline.ENFORCED),
        "metrics": dict(metrics),
        "config": {"n_topics": 1},
    }
    path.write_text(json.dumps(artifact) + "\n", encoding="utf-8")


def test_write_refuses_when_measured_below_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "baseline.json"
    monkeypatch.setattr(generate_baseline, "BASELINE_PATH", target)
    # p@1 below its 0.60 floor; everything else healthy.
    _stub_report(monkeypatch, {**_HEALTHY, "p@1": 0.55})

    assert generate_baseline._write() == 1
    assert not target.exists(), "refused --write must not create/overwrite the baseline"


def test_write_succeeds_at_or_above_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "baseline.json"
    monkeypatch.setattr(generate_baseline, "BASELINE_PATH", target)
    _stub_report(monkeypatch, _HEALTHY)

    assert generate_baseline._write() == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["metrics"] == _HEALTHY


def test_check_fails_when_committed_baseline_below_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "baseline.json"
    monkeypatch.setattr(generate_baseline, "BASELINE_PATH", target)
    # Committed p@1 0.50 is below the 0.60 floor (an already-walked-down artifact)...
    _write_baseline(target, {**_HEALTHY, "p@1": 0.50})
    # ...even though the fresh measurement is healthy and clears baseline - margin.
    _stub_report(monkeypatch, _HEALTHY)

    assert generate_baseline._check() == 1


def test_check_floor_raises_bar_above_baseline_margin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "baseline.json"
    monkeypatch.setattr(generate_baseline, "BASELINE_PATH", target)
    # Committed p@1 0.62 clears the 0.60 floor; baseline - margin would be 0.57.
    _write_baseline(target, {**_HEALTHY, "p@1": 0.62}, margin=0.05)
    # Measured 0.58 clears baseline-margin (0.57) but NOT the 0.60 floor -> must fail.
    _stub_report(monkeypatch, {**_HEALTHY, "p@1": 0.58})

    assert generate_baseline._check() == 1


def test_check_passes_on_healthy_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "baseline.json"
    monkeypatch.setattr(generate_baseline, "BASELINE_PATH", target)
    _write_baseline(target, _HEALTHY)
    _stub_report(monkeypatch, _HEALTHY)

    assert generate_baseline._check() == 0
