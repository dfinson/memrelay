#!/usr/bin/env python3
"""Regenerate or enforce the retrieval-eval precision@k baseline (E11-S4 / #21).

Usage (run from a memrelay checkout; the harness needs ``memrelay`` importable):

    python tests/eval/generate_baseline.py --write   # recompute + rewrite baseline.json
    python tests/eval/generate_baseline.py --check    # enforce the CI regression gate (default)

``--check`` recomputes precision@k over the *real* ``engine.search`` for the seeded
synthetic corpus and fails (exit 1) if any enforced metric has dropped below
``baseline - margin``. Both modes are deterministic, offline, and need no API key: the
harness injects a fixed mock LLM and a fixed offline embedder (see ``_harness.py`` /
``README.md``).

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


def _build_report() -> dict:
    # Imported lazily (after sys.path is prepared) so the sibling harness modules resolve
    # no matter the caller's working directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _harness

    return _harness.run_eval()


def _write() -> int:
    report = _build_report()
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

    print(f"[eval] precision@k regression gate (margin={margin})")
    header = f"  {'metric':<8} {'baseline':>9} {'threshold':>10} {'measured':>9}  result"
    print(header)
    print("  " + "-" * (len(header) - 2))

    failures: list[str] = []
    for metric in enforced:
        base_value = float(base_metrics[metric])
        threshold = base_value - margin
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

    if failures:
        print(f"[eval] FAIL: {len(failures)} metric(s) regressed below baseline-margin: {failures}")
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
