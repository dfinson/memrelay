# Troubleshooting

memrelay emits **structured logs** (one JSON object per line) so field issues are
diagnosable after the fact. This page explains how to turn the detail up, where the logs
go, how to read them, and the guarantee that **secrets never appear in logs**.

## Set the log level

The default level is `INFO`. Raise it to `DEBUG` to see per-record ingest decisions,
retry/backoff activity, strategy selection, and graph-query diagnostics.

Two additive, back-compatible knobs (either works; the environment variable wins):

- **Config file** — `~/.memrelay/config.toml`:

  ```toml
  [logging]
  level = "DEBUG"
  ```

- **Environment variable** (handy for a one-off run, no file edit):

  ```bash
  # PowerShell
  $env:MEMRELAY_LOGGING__LEVEL = "DEBUG"

  # bash / zsh
  export MEMRELAY_LOGGING__LEVEL=DEBUG
  ```

Accepted values are the standard levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.
An unrecognized value is **not** fatal — logging falls back to `INFO` so a typo can never
crash startup. You can confirm the resolved value with `memrelay config` (it prints the
`[logging]` block; the level is not a secret).

## Where the logs go

All logs are written to **stderr** as JSON lines — never stdout. On the MCP transport
stdout *is* the protocol channel, so logging to it would corrupt the agent connection;
keeping logs on stderr makes them safe to read while the server is running.

| Process | How it's started | Where its logs surface |
| --- | --- | --- |
| **MCP server** (`memrelay mcp`) | Spawned by your agent over stdio | The agent inherits the server's **stderr**, so these logs are visible in your agent/host's logs. |
| **Daemon, foreground** (`memrelay _serve`) | Run it yourself in a terminal | Printed straight to your terminal's **stderr**. This is the runner `memrelay start` uses internally. |
| **Daemon, detached** (`memrelay start`) | Backgrounded, fully detached | stdout/stderr are routed to the OS null device, so these logs are **not captured**. |

### Diagnosing the background daemon

Because `memrelay start` detaches the daemon and discards its output, the way to *see*
daemon logs is to run the same daemon **in the foreground** instead:

```bash
# stop the detached daemon first if one is running
memrelay stop

# run the daemon attached, with logs on your terminal (Ctrl-C to stop)
# raise the level in the same shell for maximum detail:
#   PowerShell:  $env:MEMRELAY_LOGGING__LEVEL = "DEBUG"
#   bash/zsh:    export MEMRELAY_LOGGING__LEVEL=DEBUG
memrelay _serve
```

`memrelay _serve` hosts the ingester in-process exactly like the detached daemon does, so
its logs cover both the daemon lifecycle and the ingest pipeline. Redirect them to a file
if you want to keep them: `memrelay _serve 2> memrelay.log`.

## Reading a log line

Each line is a self-contained JSON object. Typical fields:

```json
{"event": "ingester: engine.note failed (attempt 1), backing off 0.250s seq=42: ...",
 "level": "warning", "logger": "memrelay.ingest.ingester",
 "timestamp": "2025-01-01T12:00:00.000000Z"}
```

- `event` — the human-readable message (positional `%s` args already interpolated).
- `level` — `debug` / `info` / `warning` / `error` / `critical`.
- `logger` — the module that emitted it (e.g. `memrelay.ingest.ingester`,
  `memrelay.daemon.runtime`, `memrelay.engine.graphiti`), so you can tell *where* a
  problem is.
- `timestamp` — ISO-8601, UTC.
- Structured logs may carry extra key/value context alongside `event`.

To filter by level with `jq`:

```bash
memrelay _serve 2>&1 | jq 'select(.level=="warning" or .level=="error")'
```

## No secrets in logs

Redaction is applied to **every** log line — both the existing standard-library call sites
across the daemon/ingester/engine and any structured (structlog) logger — by a processor
that runs just before rendering. It reuses the same masking primitives as the
`memrelay config` output redactor, so both surfaces behave identically. Specifically:

- **Secret-named fields are masked.** Any structured field whose key looks like a secret
  (`password`, `secret`, `token`, `api_key` / `access_key`, `authorization`, `credential`,
  and similar) is replaced with `***redacted***`. The heuristic is deliberately broad and
  errs toward over-redaction.
- **Credentials embedded in connection URIs are masked.** A password inside a URI such as
  `neo4j://user:password@host:7687` — wherever it appears, including inside a message string
  or a formatted exception traceback — is rewritten to
  `neo4j://user:***redacted***@host:7687`. The scheme, user, host, and port stay visible
  (they aren't secrets and they help diagnosis); a URI with no inline password is left
  unchanged. A `@` inside the password is handled correctly and still fully masked.
- **Nested structures are scrubbed recursively**, so a secret buried inside a nested dict or
  list is masked too.

What is intentionally **kept visible** because it isn't a secret: usernames, hosts, ports,
database names, namespace/repo identifiers, and the `[llm]`/`[embeddings]` `api_key_env`
value — that is the *name* of an environment variable, never the key itself (memrelay never
reads a raw API key into config, and never logs one).

If you are about to paste logs into an issue, they are already redacted — but give them a
final glance anyway.
