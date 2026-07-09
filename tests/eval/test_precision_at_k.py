"""The retrieval-quality regression gate as a pytest (E11-S4 / #21).

Runs the offline precision@k harness over the *real* ``engine.search`` and asserts:

* every enforced metric holds at or above ``baseline - margin`` (the CI gate); and
* the eval is deterministic (a second full run yields identical metrics) and the
  synthetic-session generator is byte-stable for a fixed seed.

This lives under ``tests/`` so it also runs in the normal suite (Linux matrix +
Windows), and a dedicated ``retrieval-eval`` CI job runs the same threshold via
``generate_baseline.py --check``. Fully offline, no API key — the harness injects a
mock LLM and a deterministic embedder.
"""

from __future__ import annotations

import json
from pathlib import Path

import _generator
import _harness
import _precision
import pytest

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"


@pytest.fixture(scope="module")
def baseline() -> dict:
    assert BASELINE_PATH.is_file(), (
        f"missing baseline at {BASELINE_PATH}; regenerate with "
        "`python tests/eval/generate_baseline.py --write`"
    )
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report() -> dict:
    """Run the full offline eval once and reuse it across the module's assertions."""
    return _harness.run_eval()


def test_precision_at_k_meets_baseline(report: dict, baseline: dict) -> None:
    """The CI regression gate: no enforced metric may drop below ``baseline - margin``."""
    margin = float(baseline["margin"])
    measured = report["metrics"]
    regressions = []
    for metric in baseline["enforced"]:
        threshold = float(baseline["metrics"][metric]) - margin
        value = float(measured.get(metric, 0.0))
        if value < threshold - 1e-9:
            regressions.append((metric, value, threshold))
    assert not regressions, f"recall quality regressed below baseline-margin: {regressions}"


def test_eval_is_deterministic(report: dict) -> None:
    """A second independent full run must produce byte-identical metrics."""
    second = _harness.run_eval()
    assert second["metrics"] == report["metrics"], (
        f"non-deterministic eval: {report['metrics']} != {second['metrics']}"
    )


def test_report_config_matches_baseline(report: dict, baseline: dict) -> None:
    """The measured corpus shape must match what the baseline was recorded against."""
    assert report["config"] == baseline["config"], (
        "eval config drifted from the baseline; regenerate baseline.json if this is intended"
    )


def test_metrics_are_well_formed(report: dict) -> None:
    metrics = report["metrics"]
    assert metrics, "no metrics produced"
    assert all(0.0 <= value <= 1.0 for value in metrics.values()), f"out of range: {metrics}"


def test_generator_is_byte_stable() -> None:
    """A fixed seed yields a byte-identical corpus (labeled queries + sessions)."""
    first = _generator.generate().to_canonical_json()
    second = _generator.generate().to_canonical_json()
    assert first == second


def test_generator_labels_are_meaningful() -> None:
    """Distractors exist and every query has gold labels drawn from the corpus vocab."""
    corpus = _generator.generate()
    assert len(corpus.queries) == corpus.n_topics
    assert len(corpus.all_facts()) == corpus.n_topics * corpus.facts_per_topic
    vocab = {token.lower() for token in corpus.vocab}
    for query in corpus.queries:
        assert query.relevant, "a gold query has no relevant identities"
        assert all(identity in vocab for identity in query.relevant)


def test_precision_math() -> None:
    """Pure precision@k / hit@k arithmetic, independent of any graph build."""
    ranked = ["zephyr", "authentication", "quasar", "nimbus"]
    gold = ["zephyr", "quasar"]
    assert _precision.precision_at_k(ranked, gold, 1) == 1.0
    assert _precision.precision_at_k(ranked, gold, 2) == pytest.approx(0.5)
    assert _precision.precision_at_k(ranked, gold, 4) == pytest.approx(0.5)
    assert _precision.hit_at_k(ranked, gold, 1) == 1.0
    assert _precision.hit_at_k(["authentication"], gold, 1) == 0.0
    assert _precision.macro_average([1.0, 0.0, 0.5]) == pytest.approx(0.5)


def test_ranked_identities_dedupes_preserving_order() -> None:
    nodes = [{"name": "Zephyr"}, {"name": "zephyr"}, {"name": "Quasar"}, {"name": ""}]
    assert _precision.ranked_identities(nodes) == ["zephyr", "quasar"]
