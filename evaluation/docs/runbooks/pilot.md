# Runbook: 128-unit blinded pilot

The pilot is a fixed operating-characteristics stage. Its artifacts, analyses,
and reports are permanently `non-confirmatory`; they cannot enter the primary
estimate or relax any frozen threshold.

## Entry

`memrelay-eval run --stage pilot` requires the accepted integration exit,
independent stage authorization, and `--pilot-plan`. The sealed plan binds
exactly 16 opaque tasks with eight assignment units each (128 total), 2-3
session receipts per assignment unit, assignment seed and blocks, holdout,
blinding transform, panel/rubric, evidence matrix, limits, analysis, price,
model, catalog, and configuration hashes. It embeds the hard envelope of 32M
task-agent tokens, separate positive AI-credit and USD caps, 6M framework
input tokens, 2M framework output tokens, positive task-class active-time
caps, five local elapsed days, and concurrency two. Plan or lock drift fails
before enrollment.

Resume starts only units with no start receipt after lock, ledger/CAS, and
evidence verification. It does not restart active or terminal units and never
replaces an assignment.

## Exit

The whole pilot passes only when every mandatory evidence item is present,
overall evidence completeness is at least 98%, reliability is at least 0.70,
calibration MAE is at most 0.10, blinded-classifier upper 95% AUC is at most
0.60, and panel, blinding, security, governance, grading, evidence, and causal
gates all pass. Variance, ICC, attrition, harm, and every frozen power cell
(at least 10,000 trials each with an independent spot check) are required
publications. `memrelay-eval pilot-gate --pilot-plan <plan> --exit-evidence
<evidence> --output-root <root>` seals the exit artifact and its full evidence
digest. A rejected result exits nonzero but is still retained append-only.

Any required failure rejects the entire stage. Preserve all evidence while
blinded, repair independently, obtain independent authorization, and run a
new 128-unit stage under a fresh stage id. No complete-case subset, regrade,
favorable task selection, threshold weakening, or decoded efficacy result can
advance the pilot.
