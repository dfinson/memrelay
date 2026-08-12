# Integration stage runbook

The integration stage is infrastructure conformance, not efficacy evidence. It has
exactly 32 assigned runs: eight synthetic scenarios, locked `R-COP-M0`, two
concealed condition slots, and two repeats. Operators must not decode condition
slots, inspect efficacy outcomes, reorder a selected subset, add runs, or replace
a failed run.

## Entry

1. Verify the immutable bootstrap receipt and independently accepted conformance
   report against the exact stage locks.
2. Verify the accepted conformance exit, entry bundle, separate operator or
   scheduler authorization, catalog/config/model/price/limit locks, and frozen
   integration limits.
3. Seal the plan before starting:

   ```text
   memrelay-eval run --stage integration --entry-bundle <entry> --predecessor-exit <conformance-exit> --authorization <authorization> --conformance-report <report> --bootstrap-receipt <receipt> --integration-scenarios <scenarios> --integration-limits <limits> --output-root <artifacts>
   ```

   A presealed `--integration-plan` may be supplied instead of the two plan
   inputs. Conflicting sealed and source inputs are refused.

The plan must contain exactly 32 opaque run IDs and a pair-balanced precomputed
order. The frozen envelope is 3.2M task-agent tokens, separately frozen Copilot
AI credits, 0.6M framework input tokens, 0.2M framework output tokens plus USD,
operator-supplied per-run tool caps, 15 active minutes per run, eight local elapsed
hours, and concurrency two.

## Pause and resume

Persist planned, start, and terminal receipts through the sole control authority.
Resume only never-started planned runs after locks, receipts, reconciliation, and
backup status verify. Active attempts drain or cancel only under the frozen breaker
policy; retain all partial native, telemetry, cost, exposure, and artifact evidence.

The sole retry is a single conclusively unexposed
`infrastructure_failed_pre_exposure` retry. It retains both attempt receipts and
does not change the 32-run denominator. No post-exposure, capped, cancelled,
ambiguous, failed, or unfavorable attempt is retried or replaced.

Any cap, quota, throttle, model, lock-drift, infrastructure-rate, or evidence-loss
signal trips the shared circuit breaker and stops new starts. Diagnose using only
outcome-blind status: planned/started/terminal counts, schedule balance, breaker
state, quota/model health, cost/time headroom, reconciliation, backup, and fault
summaries.

## Exit and repair

After every retained attempt is terminal, reconcile all terminal evidence and seal
the run, reconciliation, backup, parity, cost, and fault summaries. Seal the exit:

```text
memrelay-eval integration-gate --integration-plan <plan> --exit-evidence <evidence> --output-root <artifacts>
```

Acceptance requires at least 30 of 32 infrastructure-complete assigned runs,
complete reconciled evidence for every terminal attempt, and zero categorical
blockers. The accepted or rejected decision is write-once.

On any failure, preserve all evidence, reject the old stage, repair the defect, and
request independent authorization for a fresh stage ID and fresh protocol ID. Rerun
the complete 32-run envelope. A rejected stage cannot be regraded, resumed into
acceptance, or salvaged with a favorable subset.
