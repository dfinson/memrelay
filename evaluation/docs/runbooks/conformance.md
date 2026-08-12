# Runbook: Bootstrap and conformance gate

Bootstrap and conformance establish evaluator substrate readiness before any study
enrollment. A passed report is **conformance evidence, not efficacy, safety,
economic, or release evidence**.

## Bootstrap

Run `memrelay-eval bootstrap --backup-root <second-volume-path>` only in the
isolated Python 3.13 evaluator environment with an authenticated Copilot
subscription. Bootstrap verifies the frozen SDK/runtime, Collector archive and
semantic mapping, and second-volume evidence preflight. The SDK runtime downloads
once; subsequent execution requires `COPILOT_SKIP_CLI_DOWNLOAD=1`. Never replace
a changed runtime or runtime lock automatically. It publishes one immutable
`bootstrap-receipts/<digest>.json` document. Its runtime, protocol, environment,
input, output, and terminal hashes must be supplied unchanged to conformance.

## Unpaid CI conformance

Prepare a canonical JSON object containing the twelve sealed integration-entry
hashes, then run:

```text
memrelay-eval conformance --mode unpaid_ci --stage-locks integration-locks.json \
  --bootstrap-receipt artifacts/bootstrap-receipts/<digest>.json --output-root artifacts
```

This copies the synthetic catalog to an isolated local working directory and runs
the fake catalog-to-report proof path under network denial. It opens no Copilot or
OpenAI client, strips provider credentials from every probe subprocess, and retains
an immutable report under
`conformance-reports/<report-id>.json`. Repeating identical inputs reuses the
same identity; different inputs create a new identity without changing a prior
report.

The report binds catalog, protocol, SDK/runtime, model, environment, grader,
judge, telemetry, price, limits, and preceding-exit hashes plus all mandatory
proof receipts: catalog, credentials, both workspace providers, grader, blind
judge, telemetry, CAS, second-volume backup/restore, reconciliation, replay,
Parquet, report, repricing, no-network, and cross-repository denial. Every receipt
is produced by exactly one authority-owned executable probe and contains
proof-specific observed input/output hashes, execution identity, terminal status,
and failure evidence. A missing, duplicate, skipped, unavailable, no-op, or failed
probe blocks enrollment.

## Provider qualification

Provider qualification is a separately invoked paid operation:

```text
memrelay-eval conformance --mode provider_qualification --stage-locks integration-locks.json \
  --bootstrap-receipt artifacts/bootstrap-receipts/<digest>.json \
  --entry-bundle sealed-entry.json --authorization paid-authorization.json \
  --output-root artifacts
```

It refuses CI, missing or non-paid Story 6.1 authority, stale authority, runtime
drift, or unavailable authenticated SDK capabilities before calling the official
Copilot boundary. It is never selected by ambient CI configuration.

## Enrollment authority

Every `memrelay-eval run --stage ...` request requires
`--conformance-report <path>` and `--bootstrap-receipt <path>`. Integration
additionally requires that report's locked inputs, exact bootstrap digest, runtime,
protocol, and environment all match its sealed entry bundle. Missing, malformed,
failed, tampered, incomplete, or stale authorities refuse enrollment before an
execution stage starts. A successful conformance command cannot create the separate
operator/scheduler authorization required by Story 6.1.

## Failure response

1. Stop enrollment immediately and preserve the failed report, command manifests,
   CAS evidence, and Collector fault evidence.
2. Repair the specific failed component without modifying previous reports.
3. Rerun the complete dependent proof closure. Changed observed inputs, outputs,
   runtime, protocol, environment, or implementation identity produce a new report.
4. Obtain independent operator or scheduler acceptance of the conformance exit,
   then separately authorize the integration entry bundle.

Never substitute a provider topology, suppress a failed receipt, overwrite a
report, or treat a bounded qualification sample as efficacy evidence.
