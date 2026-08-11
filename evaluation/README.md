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

## Observation sentinel qualification

Observation conformance qualifies `replay` and `file_watch` independently. Each
path freezes hashes of the current discovery/capture implementation, semantic
map, configuration, runtime lock, sentinel contract, and reconciliation policy
before injecting opaque synthetic sentinels. Evidence retains only sentinel IDs,
sequence numbers, timestamps, restart epochs, and boundary names across
discovery, capture, pre-idempotency input, spool, daemon, MCP-visible graph,
telemetry, manifests, terminal flush, and reconciliation. `file_watch` retains
its own live-tail deliveries; replay-backstop delivery cannot qualify a tail
that failed or emitted no sentinel.

A post-idempotency duplicate, missing/gapped/reordered/delayed sentinel,
restart-recovery failure, terminal-flush failure, authority conflict, or
unreconciled telemetry fails that path and emits no completeness claim.
Changing a hashed implementation or semantic-map input derives a new
conformance hash and protocol version; previously written evidence retains its
original binding. These decisions support only the named path's frozen sentinel
contract, not efficacy, safety, economics, production-wide reliability, or
cross-repository fitness.

Use `memrelay-eval observation-conformance --input <canonical-contract.json>
--product-config <product.toml> --runtime-lock <runtime-lock.json>
--output-root <artifacts-root>` to retain one immutable decision and
path-specific manifest. The canonical input contains only a prior serialized
contract identity; boundary evidence and caller-selected decision times are
rejected. At execution the evaluator rehashes the imported configured
composition, semantic map, product configuration, and runtime-lock bytes,
rejecting an identity mismatch with a typed drift failure. It then creates the
frozen window and fresh sentinels, drives the real poller/capture composition,
emits and collects value-safe telemetry from actually observed sentinels, and
retains source/product-record timestamps without post-run relabeling.

## Terminal evidence backup and restore

`memrelay-eval bootstrap --backup-root <second-volume-path>` proves a writable
independent local volume using canonical platform volume identities and an
atomic-rename probe. After a terminal attempt, the control process runs
`memrelay-eval backup-terminal` with the explicit artifact root, ledger, run,
and attempt IDs. It takes a SQLite backup-API snapshot, copies a complete
immutable CAS inventory into a staging generation, verifies every size and
SHA-256 digest, atomically publishes the generation, and appends a typed
`backup_receipt` artifact link. Existing incomplete, stale, conflicting, or
tampered generations are rejected rather than repaired.

`memrelay_eval.evidence.backup.restore_drill` restores only a published
generation into a new quarantine root. The caller must supply the current
authorization/retention/tombstone policy before reachability rebuilding or
artifact reads. It verifies the SQLite snapshot, all inventory hashes,
ledger-to-artifact links, and deterministic CAS reachability within the
24-hour RTO; any failure remains a paid-pilot blocker.
