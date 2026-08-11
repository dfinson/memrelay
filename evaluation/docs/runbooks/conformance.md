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
a changed runtime or runtime lock automatically.

## Unpaid CI conformance

Prepare a canonical JSON object containing the twelve sealed integration-entry
hashes, then run:

```text
memrelay-eval conformance --stage-locks integration-locks.json --output-root artifacts
```

This copies the synthetic catalog to an isolated local working directory and runs
the fake catalog-to-report proof path under network denial. It opens no Copilot or
OpenAI client and retains an immutable report under
`conformance-reports/<report-id>.json`. Repeating identical inputs reuses the
same identity; different inputs create a new identity without changing a prior
report.

The report binds catalog, protocol, SDK/runtime, model, environment, grader,
judge, telemetry, price, limits, and preceding-exit hashes plus all mandatory
proof receipts: catalog, credentials, both workspace providers, grader, blind
judge, telemetry, CAS, second-volume backup/restore, reconciliation, replay,
Parquet, report, and cross-repository denial.

## Enrollment authority

Every `memrelay-eval run --stage ...` request requires
`--conformance-report <path>`. Integration additionally requires that report's
locked inputs exactly match its sealed entry bundle. Missing, malformed, failed,
tampered, incomplete, or stale reports refuse enrollment before an execution
stage starts. A successful conformance command cannot create the separate
operator/scheduler authorization required by Story 6.1.

## Failure response

1. Stop enrollment immediately and preserve the failed report, command manifests,
   CAS evidence, and Collector fault evidence.
2. Repair the specific failed component without modifying previous reports.
3. Rerun the complete dependent proof closure. Changed inputs or implementation
   bytes must produce a new conformance report identity.
4. Obtain independent operator or scheduler acceptance of the conformance exit,
   then separately authorize the integration entry bundle.

Never substitute a provider topology, suppress a failed receipt, overwrite a
report, or treat a bounded qualification sample as efficacy evidence.
