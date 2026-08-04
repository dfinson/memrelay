# memrelay end-to-end smoke tests

The smoke drivers exercise paths that hermetic CI cannot prove with a real host or
provider. Live extraction consumes model quota, so run it only when explicitly validating
a release or an LLM integration. Normal development and CI should use the hermetic commands
below.

## Current status

- Copilot `borrow-host` extraction has passed live end to end on Windows: a fixture was
  observed, extracted, recalled, and memrelay's own extraction session was excluded from
  observation.
- The self-observation guard and exclusion path passed live.
- OpenAI `byo-key` missing-key behavior passed.
- A prior OpenAI live extraction received a valid API response but failed in memrelay's
  adapter because graphiti's low-level response tuple was treated as the final mapping.
  The adapter now delegates through graphiti's public `generate_response()` API and has a
  hermetic regression test. Do not claim the live OpenAI path is revalidated until a
  separately authorized live run passes.
- Daemon startup uses adaptive readiness windows: 30 seconds for a warm graph and 120
  seconds for a cold graph. `MEMRELAY_READY_TIMEOUT` can override either. Detached daemon
  output is captured at `<MEMRELAY_HOME>/logs/daemon-startup.log`.

## Hermetic validation

These commands do not invoke Copilot, Claude, OpenAI, or another inference provider:

```powershell
# from the repository root
python -m pytest
ruff check .
ruff format --check .

# no-LLM first-run/MCP smoke
python scripts\smoke_e2e.py --phase1-only
```

`--phase1-only` validates the isolated home, daemon lifecycle, MCP tool surface, and empty
recall without performing extraction.

## Isolated first-run smoke

Use a disposable home so the smoke cannot read or write the user's graph:

```powershell
$env:MEMRELAY_HOME = "$PWD\.smoke-home"
$env:PYTHONPATH = (Resolve-Path .\src).Path

memrelay init
memrelay start
memrelay status
python scripts\smoke_e2e.py --phase1-only
memrelay stop
Remove-Item -Recurse -Force $env:MEMRELAY_HOME
```

With no explicit `[graph] path`, the embedded graph is
`<MEMRELAY_HOME>/graph.db`. An explicit config path or `MEMRELAY_GRAPH__PATH` remains an
override and is not redirected.

Cold startup can legitimately take longer than a warm restart while the embedder,
LadybugDB, and FTS extension initialize. `memrelay start` waits up to 120 seconds on that
path. If it reports that the daemon is still starting:

```powershell
memrelay status
Get-Content "$env:MEMRELAY_HOME\logs\daemon-startup.log"   # MEMRELAY_HOME is set above
```

Set a larger positive readiness window only when the log shows healthy progress on a slow
machine:

```powershell
$env:MEMRELAY_READY_TIMEOUT = "240"
memrelay start
```

## Live Copilot borrow-host smoke

> **Quota warning:** this invokes the authenticated `copilot` CLI several times. It is a
> manual test and must not run in CI.

The dedicated driver observes exactly one synthetic session from a throwaway
`MEMRELAY_COPILOT_HOME`. It never starts a live poller against the real `~/.copilot`, so
memrelay cannot recursively observe the extraction sessions created by `copilot -p`.

Prerequisites:

- an authenticated `copilot` CLI on `PATH`;
- enough quota for several sequential extraction calls;
- network access needed by the host CLI and any uncached embedding/FTS assets.

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python scripts\smoke_selfobserve_e2e.py
```

The script removes inherited `*_API_KEY` variables, creates disposable memrelay and
Copilot-observation homes, runs one observe/spool/ingest/recall cycle, and prints an
evidence block. Success requires:

1. the circular-config warning to fire;
2. a registered extraction session to be excluded while the fixture remains observable;
3. at least one new internal Copilot session id to be registered by the real extraction;
4. the recalled graph to contain one of the fixture's distinctive tokens.

One episode performs several host calls. The drain uses one absolute 1200-second budget
because a successful extraction has been observed at 927.7 seconds. Override it explicitly
for a slower or faster validation environment:

```powershell
python scripts\smoke_selfobserve_e2e.py --drain-timeout 1500
```

On timeout or cancellation, the ingester is cancelled and the exact host subprocess
created by memrelay is terminated and awaited. The cleanup never kills processes by name.

## Live OpenAI byo-key smoke

> **Billing warning:** this uses the configured OpenAI account. Do not run it without
> explicit authorization.

Configure a disposable home and select `byo-key`:

```toml
[graph]
backend = "ladybug"

[llm]
strategy = "byo-key"
provider = "openai"
api_key_env = "OPENAI_API_KEY"
model = "gpt-4o-mini"

[embeddings]
provider = "local"
model = "BAAI/bge-small-en-v1.5"
```

The missing-key check is safe and should fail clearly only when extraction is requested:

```powershell
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
memrelay start
# search/health remain usable; memory_note must report that OPENAI_API_KEY is not set
memrelay stop
```

The successful live OpenAI path is intentionally not marked green in this document. Its
adapter contract is covered hermetically; update this status only after an authorized live
`memory_note` and `memory_recall` round trip succeeds.

## Expected failures and diagnostics

| Symptom | Action |
|---|---|
| `memrelay start` says the daemon is still starting | Run `memrelay status`, then inspect `<MEMRELAY_HOME>/logs/daemon-startup.log`; use `MEMRELAY_READY_TIMEOUT` only for demonstrated slow startup. |
| `host command 'copilot' not found` | Install/authenticate Copilot or configure another LLM strategy. |
| `byo-key LLM: environment variable ... is not set` | Export the configured key only if a live billed test was authorized. |
| Live smoke drain timeout | Inspect the logged elapsed work; rerun with `--drain-timeout` only when additional quota/time is intentional. Cleanup has already cancelled the ingester and its owned host process. |
| Empty recall after extraction | Inspect daemon/driver logs and ingestion counters; do not treat spool drain alone as proof that entity extraction succeeded. |
