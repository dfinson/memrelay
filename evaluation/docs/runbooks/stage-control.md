# Runbook: Stage control (Epic 6.1)

This runbook governs the operational gate layer that seals stage bundles and
enforces the non-interactive stage CLI. It is descriptive of the frozen
contracts implemented in `memrelay_eval.orchestration.stages`,
`memrelay_eval.orchestration.limits`, `memrelay_eval.domain.policies`, and the
`memrelay-eval run --stage ...` command. Alerts pause or stop new work; they
never mutate, delete, or relax existing evidence.

## Stage progression and authority split

The fixed progression is:

```
bootstrap / conformance -> integration (32 runs) -> pilot (128, blinded)
  -> primary (512) -> secondary (<= 192 total, 96 per role)
```

The 24-cluster cross-repository stage stays unreachable until primary completion
plus a complete, current DG-R bundle, and it remains denied by default in the
CLI (Story 7.3, `SCOPE-R-DENY`).

Authority is split across three independent facts; no single one promotes a
stage:

1. **Process completion** — a stage's units finished. This alone is never
   authorization.
2. **Exit acceptance** — an immutable exit bundle records `accepted` only when it
   carries reconciliation and inclusion-decision receipts (`OPS-PROTOCOL-FREEZE`).
3. **Independent authorization** — an operator or scheduler seals a stage
   authorization scoped to one stage id, protocol, frozen entry digest, and
   envelope, before the stage runs. The execution command never mints it and a
   process can never authorize its own next stage.

## Sealed bundles (AC 1)

`StageEntryBundle` binds exact hashes for catalog, protocol, SDK, runtime lock,
model lock, environment, grader, judge, telemetry, price table, limits, and the
accepted preceding-exit digest (`OPS-STAGE-ENVELOPE`). The bundle is
content-addressed; `StageBundleStore` writes each seal once. A reseal with
identical bytes is idempotent (`reused`); a conflicting reseal fails closed with
`stage_bundle_mutation`. Any legitimate change requires a **new protocol id or a
new stage id**, never an in-place patch.

## Entry guard (AC 2)

`memrelay-eval run --stage integration|pilot|primary|secondary` fails closed
**before enrollment** with a typed status when any of the following hold:

| Condition | Typed code |
| --- | --- |
| Predecessor exit missing | `missing_predecessor_exit` |
| Predecessor exit corrupt / tampered | `predecessor_exit_corrupt` |
| Predecessor exit rejected | `predecessor_exit_rejected` |
| Predecessor exit not accepted-and-complete | `predecessor_exit_incomplete` |
| Entry lock does not link the predecessor digest | `predecessor_exit_link_mismatch` |
| Wrong predecessor stage kind (skip) | `stage_skipped` |
| Authorization scope / envelope mismatch | `authorization_scope_mismatch`, `authorization_envelope_mismatch` |
| Authorizer role not operator/scheduler (self-authorization) | `self_authorization_denied` |
| Authorization expired | `stale_authorization` |
| Inputs incomplete | `stage_inputs_incomplete` |
| Ambient stage configuration present | `ambient_stage_configuration_forbidden` |
| Paid execution attempted under CI | `paid_execution_forbidden_in_ci` |

There is **no automatic topology fallback** and **no self-promotion**. The
`--stage cross-repo` request is denied before discovery and leaks no repository
or credential material.

## Command manifests (AC 3)

Every terminal path of a non-interactive command emits exactly one append-only
command manifest (`command-manifest.schema.json`) binding: command identity,
stage, terminal status, exit code, input hashes, output hashes, runtime lock,
protocol id, and error code, plus a canonical digest. Idempotent replays produce
the same digest and reuse the same manifest file. Paid execution requires
explicit operator invocation or approved scheduling and never runs in CI
(`OPS-QUOTA-RATE-LIMIT`, AD-22).

## Pause / resume (idempotent)

`plan_stage_resume` returns only unfinished planned unit ids and fails closed on
any unverified precondition: an open circuit breaker
(`resume_circuit_breaker_open`), a stale authorization (`stale_authorization`),
lock drift (`resume_lock_drift`), receipt conflict (`resume_receipt_conflict`),
or ledger/CAS conflict (`resume_ledger_cas_conflict`). Terminal units are never
rerun and no attempt is replaced; partial evidence is preserved untouched.

## Monitor status (outcome-blind)

`stage_status_projection` emits a treatment-neutral, outcome-blind local status
document: stage state, planned/started/terminal counts, reconciliation
completeness, quota/cost/time headroom, throttle and model health, evidence-loss
signals, and authorization expiry. It records no outcome, no treatment label, and
no repository or credential material. `pause_new_work` is set when the envelope is
exhausted, the authorization has expired, evidence-loss signals appear, or
throttle/model health degrades (`STAT-FIXED-LOOK`).

## Alerts and operator actions

`stage_alert_actions()` returns the frozen alert-to-action map. Alerts pause or
stop new work; they never mutate evidence.

| Alert | Operator action |
| --- | --- |
| `lock_drift` | Pause new work; require a new protocol or stage identity. |
| `incomplete_evidence` | Pause new work; reconcile before any acceptance. |
| `categorical_blocker` | Stop the affected family; never relax a threshold. |
| `exhausted_envelope` | Stop new attempts; a new sealed envelope is required. |
| `stale_authorization` | Pause new work until an operator or scheduler re-authorizes. |
| `backup_failure` | Pause paid work until backup and restore proof passes. |
| `dg_r_revocation` | Disable the entire cross-repository stage. |

## TEA stable coverage IDs

`OPS-PROTOCOL-FREEZE`, `OPS-STAGE-ENVELOPE`, `OPS-QUOTA-RATE-LIMIT`,
`STAT-FIXED-LOOK`, `SCOPE-R-DENY`.
