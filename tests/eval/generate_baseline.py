#!/usr/bin/env python3
"""Regenerate or enforce the retrieval-eval precision@k baseline (E11-S4 / #21).

Usage (run from a memrelay checkout; the harness needs ``memrelay`` importable):

    python tests/eval/generate_baseline.py --write   # recompute + rewrite baseline.json
    python tests/eval/generate_baseline.py --check    # enforce the CI regression gate (default)

``--check`` recomputes precision@k over the *real* ``engine.search`` for the seeded
synthetic corpus and fails (exit 1) if any enforced metric has dropped below
``max(baseline - margin, absolute_floor)``. Both modes are deterministic, offline, and need
no API key: the harness injects a fixed mock LLM and a fixed offline embedder (see
``_harness.py`` / ``README.md``).

The regression gate reads ``margin``/``enforced`` back from ``baseline.json`` (so tightening
them is a visible artifact edit), but the **absolute floors** (:data:`_ABSOLUTE_FLOORS`) live
only in code. ``--write`` refuses to commit a baseline whose measured metrics fall below the
floor, and ``--check`` fails if either the committed baseline or the freshly measured metrics
dip beneath it — so the gate cannot be silently walked down via repeated ``--write`` runs.

Locally, run with this worktree's ``src`` on ``PYTHONPATH`` (never ``pip install -e .``
in the shared env), e.g. ``$env:PYTHONPATH = (Resolve-Path .\\src).Path``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"

#: Absolute drop tolerated before the gate fails, and which metrics it guards. Baked into
#: baseline.json on --write; --check reads them back from the file (the checked-in source of
#: truth), so tightening the gate is a visible edit to the artifact.
MARGIN = 0.05
ENFORCED = ("p@1", "p@3", "p@5", "hit@3", "hit@5")

#: Hardcoded absolute lower bounds the baseline must never drop beneath. Unlike
#: ``MARGIN``/``ENFORCED`` (which ``--check`` reads back from ``baseline.json``), these live
#: ONLY here in code: a walked-down artifact therefore cannot also lower its own floor.
#: ``--write`` refuses to write a baseline whose measured metrics fall below these, and
#: ``--check`` fails if either the committed baseline OR the freshly measured metrics dip
#: beneath them. Set comfortably below the current baseline (p@1 0.75, p@3 0.667, p@5 0.40,
#: hit@3/hit@5 1.0) so they bite only on a genuine regression, not run-to-run noise. Keys must
#: be a subset of ``ENFORCED``.
_ABSOLUTE_FLOORS = {
    "p@1": 0.60,
    "p@3": 0.50,
    "p@5": 0.30,
    "hit@3": 0.80,
    "hit@5": 0.80,
}
assert set(_ABSOLUTE_FLOORS) <= set(ENFORCED), "every floored metric must also be enforced"


def _below_floor(metrics: dict) -> list[tuple[str, float, float]]:
    """Return ``(metric, value, floor)`` for every floored metric under its absolute floor."""
    breaches: list[tuple[str, float, float]] = []
    for metric, floor in _ABSOLUTE_FLOORS.items():
        value = float(metrics.get(metric, 0.0))
        if value < floor - 1e-9:
            breaches.append((metric, value, floor))
    return breaches


def _build_report() -> dict:
    # Imported lazily (after sys.path is prepared) so the sibling harness modules resolve
    # no matter the caller's working directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _harness

    return _harness.run_eval()


def _write() -> int:
    report = _build_report()
    measured = report["metrics"]

    # An absolute floor --write cannot walk beneath: refuse to commit a baseline whose measured
    # metrics have fallen below the hardcoded floor, rather than silently ratcheting the gate down.
    breaches = _below_floor(measured)
    if breaches:
        print(
            "[eval] REFUSING to write: measured metric(s) below the hardcoded absolute floor "
            "(a lower bound --write cannot walk beneath):",
            flush=True,
        )
        for metric, value, floor in breaches:
            print(f"  {metric:<8} measured={value:.4f} floor={floor:.4f}")
        return 1

    artifact = {
        "description": (
            "Retrieval quality regression baseline for memrelay recall (E11-S4, issue #21). "
            "precision@k / hit@k over engine.search, macro-averaged across seeded gold queries. "
            "See tests/eval/README.md for exactly what this measures."
        ),
        "generated_by": "python tests/eval/generate_baseline.py --write",
        "margin": MARGIN,
        "enforced": list(ENFORCED),
        "metrics": report["metrics"],
        "config": report["config"],
    }
    BASELINE_PATH.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"[eval] wrote baseline -> {BASELINE_PATH}")
    print(json.dumps(artifact["metrics"], indent=2))
    return 0


def _check() -> int:
    if not BASELINE_PATH.is_file():
        print(f"[eval] FAIL: baseline missing at {BASELINE_PATH} (run --write first)", flush=True)
        return 1

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    margin = float(baseline.get("margin", MARGIN))
    enforced = baseline.get("enforced", list(ENFORCED))
    base_metrics = baseline["metrics"]

    report = _build_report()
    measured = report["metrics"]

    # Absolute floor is code-only (never read from the artifact): detect a *committed* baseline
    # that was itself walked below the floor, independent of how the fresh measurement lands.
    committed_breaches = _below_floor(base_metrics)

    print(f"[eval] precision@k regression gate (margin={margin})")
    header = f"  {'metric':<8} {'baseline':>9} {'threshold':>10} {'measured':>9}  result"
    print(header)
    print("  " + "-" * (len(header) - 2))

    failures: list[str] = []
    for metric in enforced:
        base_value = float(base_metrics[metric])
        floor = _ABSOLUTE_FLOORS.get(metric)
        threshold = base_value - margin
        if floor is not None:
            threshold = max(threshold, floor)
        value = float(measured.get(metric, 0.0))
        ok = value >= threshold - 1e-9
        if not ok:
            failures.append(metric)
        status = "PASS" if ok else "FAIL"
        print(f"  {metric:<8} {base_value:>9.4f} {threshold:>10.4f} {value:>9.4f}  {status}")

    # Context: report every measured metric, including the non-enforced ones.
    extras = [m for m in measured if m not in enforced]
    if extras:
        print("  (context) " + ", ".join(f"{m}={measured[m]}" for m in extras))

    if committed_breaches:
        print(
            "[eval] FAIL: committed baseline.json is itself below the hardcoded absolute floor "
            "(the baseline was silently walked down):"
        )
        for metric, value, floor in committed_breaches:
            print(f"  {metric:<8} committed={value:.4f} floor={floor:.4f}")

    if failures:
        print(f"[eval] FAIL: {len(failures)} metric(s) regressed below the gate: {failures}")
    if failures or committed_breaches:
        return 1
    print("[eval] PASS: recall quality holds at or above the baseline.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate or enforce the precision@k baseline.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="recompute and overwrite baseline.json")
    group.add_argument(
        "--check",
        action="store_true",
        help="enforce the regression gate against baseline.json (default)",
    )
    args = parser.parse_args(argv)
    return _write() if args.write else _check()


if __name__ == "__main__":
    raise SystemExit(main())
