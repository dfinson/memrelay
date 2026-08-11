# Circuit breakers

## Scope

Circuit breakers are local, sole-writer admission controls for a sealed evaluation
stage. They stop new attempts; they do not roll back, delete, replace, reschedule,
or promote a run. The frozen envelopes cover Copilot tokens, tools, AI credits,
framework input and output tokens, framework USD, active and elapsed time, quota,
throttle, model availability, infrastructure failures, and evidence loss. Per-run
and stage envelopes have distinct source hashes and are never transferred between
models or providers.

## On trip

1. Preserve the append-only `tripped` record and its source hashes before acting.
2. Stop all new admissions. Do not select a fallback model, provider, repository,
   threshold, order, or concurrency.
3. Move started attempts to `draining`; allow them to terminate or cancel only
   under their frozen policy.
4. Retain each terminal's classification, partial native/ledger/CAS/telemetry
   evidence, cost quantities, and exposure state in ITT. Never replace a capped,
   canceled, ambiguous, or post-exposure attempt.
5. Reconcile retained evidence, verify backup health, and report quota, throttle,
   model, provider-contention, infrastructure, evidence-loss, order, concurrency,
   and arm-balance strata separately.
6. For governance revocation, isolate credentials if required and stop
   cross-repository work immediately. Do not use another repository.

## Resume

A closed breaker remains closed until an independent operator or scheduler issues
a new authorization scoped to the same stage and breaker reason. The controller
requires repair evidence, unchanged stage locks, healthy reconciliation and backup,
and remaining headroom before it reopens admission. A changed limit, price table,
runtime, model, provider, capability, or threshold requires a new protocol or
stage identity rather than a resume.
