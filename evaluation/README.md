# memrelay evaluator

This independently packaged Python 3.13 project contains the treatment-neutral
evaluation domain foundation. It is intentionally separate from the memrelay product
wheel and currently supports only deterministic, unpaid conformance paths.

```powershell
uv sync --extra dev
uv run memrelay-eval --help
uv run pytest
```

The `future-adapters` extra records frozen dependency pins for later stories. This
story does not import, execute, or configure those adapters.

## Explicit configuration and enrollment freeze

Evaluator configuration is resolved only from explicit CLI arguments, frozen
protocol/stage values, an evaluator TOML file, and safe defaults—in that order.
Environment variables are never a configuration layer. Credential references
name an environment variable and its exact target process, but never contain or
read the credential value.

```powershell
memrelay-eval effective-config --config evaluator.toml --stage conformance `
  --credential-reference OPENAI_API_KEY:memrelay_framework_daemon
```

The command prints a canonical, redacted configuration projection. Pre-enrollment
freeze persists canonical artifact identities for the effective configuration,
environment stratum, catalog, model catalog, assignment inputs, ordered inputs,
blocks, and price tables. Fixture-backed stores remain unpaid-conformance evidence
and cannot authorize study inclusion.

## Paired agent and environment parity

Before either paired attempt receives a task, the evaluator canonicalizes a
prompt-free parity record. It binds the official SDK/runtime and model locks,
model capabilities and controls, exact prompt-component hashes, tool schemas,
permissions, network policy, limits, workspace topology, built-in memory and
cross-session store settings, retry policy, effective configuration, and the
host environment stratum.

Only opaque, protocol-bound content and access deltas may differ. Any other
mismatch is persisted as typed pre-exposure evidence, appends an immutable
pre-exposure infrastructure terminal record, and prevents the Inspect
scheduler from delivering a task or starting inference. Environment
fingerprints are linked to protocols and cannot be aggregated across strata
without an explicit separate stratum.

## Terminal evidence reconciliation

`memrelay-eval reconcile --stage <stage>` consumes a canonical,
schema-versioned terminal-evidence input (or
`artifacts/reconciliation/<stage>.input.json` by default). It verifies
the control-owned ledger's immutable authority for the exact run and attempt
before using that input. The authority fixes the matrix conditions, identities,
frozen hashes, protocol/runtime hashes, and every evidence-projection CAS
reference. Each projection canonically binds its evidence kind, source
authority, status, claims, blockers, unavailable reason, and raw
manifest-backed bytes. A missing or mismatched authority/projection fails
closed; the command never creates one from CLI input. It writes an immutable,
redacted reconciliation report and submits one typed included or excluded
decision through the control-owned ledger. The command emits a canonical
manifest with input, output, runtime, and protocol hashes, and makes no
provider or analysis calls.
