"""Preflight guard for opt-in phase enrichment (E2-S6 #98).

memrelay defaults ``IngestConfig.enable_phase = False`` — the zero-config path is
deterministic and offline and never loads an ML model. When an operator opts in,
traceforge's phase inferencer becomes the *only* phase producer, and it **raises
rather than degrading** when its inputs are missing. That failure surfaces in two
different, both-unacceptable ways inside ``EventPipeline``:

* **Missing model bundle** (the packaged ``phase-model.joblib`` is absent / the
  ``TRACEFORGE_PHASE_MODEL`` override points nowhere): the pipeline loads the model
  lazily in ``new_stream`` — *outside* its per-event ``try/except`` — so the
  ``FileNotFoundError`` propagates out of ``push`` and **crashes the observe run.**
* **Missing ML deps** (``model2vec`` / its transitive ``requests`` import, or the
  vendored embedder dir): the embedding step raises *inside* the per-event
  ``try/except``, so every event is emitted **unstamped and silently** — the feature
  looks "on" but writes no phase.

:func:`resolve_phase` collapses both into one explicit, logged decision *before* the
pipeline is built: it constructs the inferencer, forces the bundle to load, and runs
one trial embed. On success it returns the **warm** inferencer to hand to
``EventPipeline(phase_inferencer=...)``. On *any* failure it logs loudly and returns
``(False, None)`` so that observe pass runs phase-off — never crashing the daemon and
never silently no-op'ing. The inferencer factory and the trial are injectable so unit
tests exercise every branch without touching the real model.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: Builds the phase inferencer to preflight. Injectable for tests.
PhaseInferencerFactory = Callable[[], Any]
#: Exercises an inferencer's real load + embed path; raises on any failure.
PhaseTrial = Callable[[Any], None]


def _default_factory() -> Any:
    from traceforge.phase.inferencer import PhaseInferencer

    return PhaseInferencer()


def _default_trial(inferencer: Any) -> None:
    """Force the two runtime load points the live pipeline would hit.

    ``inferencer.model`` triggers the joblib bundle load (the pipeline's uncaught
    crash path); ``embed_texts`` triggers the vendored model2vec embedder + its
    transitive imports (the pipeline's silent-unstamped path). Either raises here
    if its input is missing, which :func:`resolve_phase` catches and degrades on.
    """
    _ = inferencer.model
    from traceforge.phase.features import embed_texts

    embed_texts(["memrelay phase preflight"])


def resolve_phase(
    cfg: Any,
    *,
    enabled: bool | None = None,
    factory: PhaseInferencerFactory | None = None,
    trial: PhaseTrial | None = None,
) -> tuple[bool, Any]:
    """Decide whether phase enrichment is active for one observe pass.

    Returns ``(active, inferencer)``:

    * ``(False, None)`` when phase is off (``enabled`` override, else
      ``cfg.ingest.enable_phase``) — the default; no model is touched.
    * ``(True, inferencer)`` when phase is on **and** a preflight (load bundle + one
      trial embed) succeeds. The returned inferencer is warm and is passed to
      ``EventPipeline(phase_inferencer=...)`` (an explicit inferencer takes
      precedence over the pipeline's ``enable_phase`` flag).
    * ``(False, None)`` when phase is on but the preflight fails (missing bundle or
      missing ML deps). The failure is logged at ERROR with a traceback; the observe
      pass then runs phase-off instead of crashing or silently stamping nothing.

    ``factory`` / ``trial`` are injectable so tests cover every branch without the
    real model. This function never raises: the whole point is to keep an
    opt-in-but-broken phase config from taking down the ingest pipeline.
    """
    requested = cfg.ingest.enable_phase if enabled is None else enabled
    if not requested:
        return (False, None)

    make = factory or _default_factory
    run_trial = trial or _default_trial
    try:
        inferencer = make()
        run_trial(inferencer)
    except Exception:  # noqa: BLE001 — degrade on ANY failure, never crash observe
        logger.error(
            "enable_phase=true but the phase model preflight failed; this observe "
            "pass will run WITHOUT phase enrichment (episodes stored with phase=None). "
            "Install the optional deps with `pip install memrelay[phase]` and ensure "
            "the traceforge phase bundle is present (or set $TRACEFORGE_PHASE_MODEL). "
            "Run `memrelay init` for a readiness check.",
            exc_info=True,
        )
        return (False, None)

    logger.info("Phase enrichment active: traceforge phase model loaded and verified.")
    return (True, inferencer)
