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
