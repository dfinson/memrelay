# memrelay

Portable, persistent memory for your coding agents — powered by [Graphiti](https://github.com/getzep/graphiti) and [TraceForge](https://github.com/dfinson/traceforge).

memrelay automatically observes your agent sessions — Copilot CLI, Claude Code, Codex, Cursor/Continue, Cline, Aider, and more — extracts knowledge into a single local graph, and surfaces relevant memories on demand. **Memory made in one agent is recalled in another.** No memory files. No manual summaries. No graph terminology.

## What it does

**Automatically observes:**
- User prompts and assistant responses
- Tool calls and command executions
- File reads and writes
- Git commits and pull request discussions

**Retrieves on demand:**
- Relevant context when the agent asks for it (`memory_recall`)
- Cross-session continuity (remembers what you worked on yesterday)
- Cross-repo knowledge (patterns from one project inform another)
- Cross-agent knowledge (a fix learned in Claude Code surfaces in Copilot)

**Exposes three MCP tools to the agent:**

| Tool | Purpose |
| --- | --- |
| `memory_recall` | Semantic search across all memories |
| `memory_detail` | Expand a specific entity or relationship |
| `memory_note` | Explicitly save something for later |

The agent decides when to call these tools — you don't manage memory manually.

## Install

> **Under active development (v0.0.1, not yet on PyPI).** Install from source for
> now — clone this repo and `pip install -e ".[dev]"` (see [Development](#development)).
> The `pip install memrelay` flow below is the intended experience once published.

```bash
pip install memrelay
memrelay init      # creates ~/.memrelay/, auto-detects your agents, registers the MCP server with each
memrelay start     # starts the background daemon
```

Then just use your agent normally:

```bash
copilot        # or: claude, codex, cursor, aider, …
```

Memory is automatic. The daemon observes sessions in the background and the MCP server provides memory tools to every registered agent.

## Teach your agent when to recall

Memory only helps if your agent actually calls `memory_recall` at the right moments. memrelay can append a short, **opt-in** guidance block to an agent's instructions file so it knows to pull in prior context *before* starting work and to note what it learned *after*:

```bash
memrelay guidance                    # preview + confirm, then append to ./AGENTS.md
memrelay guidance --dry-run          # just show what would be written
memrelay guidance --target claude    # write to ./CLAUDE.md instead
memrelay guidance --target copilot   # write to ./.github/copilot-instructions.md
memrelay guidance --path FILE        # write to any explicit instruction file
```

memrelay never edits an instruction file without your explicit run and confirmation (pass `--yes` to skip the prompt in scripts; `--dry-run` writes nothing). The guidance lives in a fenced `<!-- memrelay:guidance:… -->` block, so re-running updates it **in place** and never touches your own content. Run it from your repo root — memory is scoped to that repo by default.

## Zero configuration

The default stack requires **zero API keys**:

| Component | Default | What it does |
| --- | --- | --- |
| Graph database | Kuzu (embedded, local file) | Stores the knowledge graph |
| LLM | borrow-host — reuse an agent's own model (e.g. your Copilot subscription) | Entity extraction, summarization |
| Embeddings | fastembed (ONNX, CPU, ~67MB) | Semantic similarity for retrieval |

Everything runs locally. No Docker, no Neo4j, no cloud services. Prefer your own key? Switch the LLM strategy to `byo-key` (see Configuration). A fully local LLM strategy (`local` — Ollama/llama.cpp) is planned but **not yet implemented** ([#64](https://github.com/dfinson/memrelay/issues/64)).

> **Backend note:** Kuzu is memrelay's committed graph backend today, but it is deprecated upstream in graphiti-core 0.29.2; a successor backend is tracked in [#76](https://github.com/dfinson/memrelay/issues/76).

## Architecture

```
┌──────────────────────────┐          ┌──────────────────────────┐
│  Any coding agent        │          │  memrelay daemon         │
│  Copilot / Claude /      │          │  (background)            │
│  Codex / Cursor / …      │          │                          │
│                          │          │  Provider watchers       │
│  ┌────────────┐          │          │  → traceforge pipeline   │
│  │ MCP Server │          ┼──socket──┤  → Graphiti ingestion    │
│  │ (tools)    │          │          │  → Kuzu graph DB         │
│  └────────────┘          │          │                          │
└──────────────────────────┘          └──────────────────────────┘
```

**Two processes:**
- **Daemon** — persistent background process that owns the Kuzu database and Graphiti instance. Watches session files, ingests events, answers queries via Unix socket.
- **MCP server** — spawned by each agent as a stdio subprocess. Thin client that forwards tool calls to the daemon.

This split exists because Kuzu requires exclusive file access — only one process can open the database.

## CLI commands

```bash
memrelay init                        # First-time setup
memrelay start                       # Start daemon (background)
memrelay stop                        # Stop daemon
memrelay status                      # Health: sessions, episodes, spool depth
memrelay observe                     # Replay a discovered session through the pipeline into the spool
memrelay seed                        # Bootstrap memory from a repo's git history (one episode per commit)
memrelay guidance                    # Append opt-in recall guidance to an agent's instructions file
memrelay config                      # Show current config

# Planned — not yet implemented (currently stubs):
memrelay forget --repo owner/name    # Delete memories for a repo
memrelay forget --namespace name     # Delete entire namespace
```

`memrelay observe` accepts `--session ID` (default: the most recently updated session), `--spool PATH` (default `<home>/spool/spool.db`), and `--copilot-home PATH`.

`memrelay seed` bootstraps memory from a repo's git history so you get useful recall on day one, before live sessions accrue. It reads `git log` and writes one episode per commit — subject, body, author, ISO date, and the touched file paths (no diffs, no GitHub API data) — to the same durable spool `observe` uses, which the daemon then drains into the graph. It is **idempotent**: each commit gets a stable key, so re-running never double-ingests. Flags: `--path DIR` (repo to read, default: current directory), `--max-count N` (most-recent commits, default 500), `--repo OWNER/NAME` / `--namespace NAME` (override the target, mirroring `forget`; by default the namespace resolves exactly as a live session in that repo would), `--spool PATH`, and `--dry-run` to preview without writing.

## Configuration

Config lives at `~/.memrelay/config.toml`. Defaults work out of the box — only override if you need to.

```toml
[graph]
backend = "kuzu"
path = "~/.memrelay/graph.db"

[llm]
strategy = "borrow-host"   # reuse an agent's own model (e.g. Copilot), no API key
host = "copilot"

[embeddings]
provider = "local"
model = "BAAI/bge-small-en-v1.5"

[ingest]
enable_phase = false
enable_boundary = false
```

**Override the LLM strategy** — `byo-key` for direct API keys (faster inference, native structured output). A `local` fully-offline strategy (Ollama/llama.cpp) is planned but not yet implemented ([#64](https://github.com/dfinson/memrelay/issues/64)):

```toml
[llm]
strategy = "byo-key"
provider = "openai"
api_key_env = "OPENAI_API_KEY"
model = "gpt-4o-mini"
```

**Group repos into a shared namespace** *(optional)* — memories are grouped by *namespace*. Add `[namespaces.<name>]` sections to make several repos share one namespace (and therefore one pool of memory). Omit this entirely and grouping is unchanged from the zero-config default:

```toml
[namespaces.acme]
repos = ["acme/api", "acme/web", "acme/shared"]

[namespaces.personal]
repos = ["me/dotfiles"]
```

This declares a repo→namespace map that memrelay consults when grouping an observed session into a namespace, ahead of its default derivation (GitHub owner, then OS username). Rules (all enforced when the config loads, with a message naming the offending namespace or repo):

- **Repo keys are `"owner/name"`, matched case-insensitively.** They're normalized to lowercase, so `Acme/API` and `acme/api` are the same repo.
- **A repo may belong to at most one namespace** — assigning the same repo to two namespaces is an error.
- **`repos` must be a list of `"owner/name"` strings** — each with exactly one `/` and a non-empty owner and name.
- **Namespace names are used verbatim** (only surrounding whitespace is trimmed) and must be non-empty.

**Adjust logging** *(optional)* — memrelay emits structured JSON logs to stderr. The
default level is `INFO`; raise it for field diagnosis, via config or the
`MEMRELAY_LOGGING__LEVEL` environment variable:

```toml
[logging]
level = "DEBUG"   # DEBUG | INFO | WARNING | ERROR | CRITICAL
```

Secrets (passwords, tokens, API keys, credentials embedded in connection URIs) are never
logged. See [docs/troubleshooting.md](docs/troubleshooting.md) for where logs go and how to
read them.

## Dependencies

memrelay depends on [TraceForge](https://github.com/dfinson/traceforge) (PyPI: `traceforge-toolkit`) for multi-agent session capture and normalization. TraceForge already normalizes ~18 agents to a common event model; memrelay handles memory.

| Dependency | Purpose |
| --- | --- |
| `traceforge-toolkit` | Multi-agent event normalization (~18 agents) |
| `graphiti-core` | Knowledge graph engine |
| `kuzu` | Embedded graph database |
| `fastembed` | Local ONNX embeddings |
| `mcp` | Model Context Protocol server |

## How it works

1. **Observe** — Each agent provider discovers active sessions (Copilot reads the per-session `~/.copilot/session-state/<id>/events.jsonl` trace, falling back to `~/.copilot/session-store.db`; other agents read their own store or logs) and the daemon tails them for events
2. **Normalize** — Events pass through a TraceForge pipeline (parse → enrich → filter) into a common `SessionEvent` model, regardless of which agent produced them
3. **Ingest** — Filtered events are written to a durable SQLite spool, then batch-ingested into Graphiti
4. **Extract** — Graphiti extracts entities, relationships, facts, and temporal information
5. **Retrieve** — When the agent calls `memory_recall`, the MCP server queries the daemon, which searches Graphiti using hybrid semantic + graph search
6. **Format** — Results are formatted as structured markdown and injected into the agent's context

## Design principles

- **Zero config** — works with `pip install` + `memrelay init`
- **Local-first** — everything runs on your machine, no cloud required
- **Invisible** — when working correctly, you never think about memory
- **Portable** — one memory graph shared across every agent you use
- **Thin layer** — memrelay is integration glue around Graphiti and TraceForge, not a replacement

## Development

Requires Python 3.11–3.13. For the full contributor guide — dev setup, the test suite,
linting, and the CI matrix — see [CONTRIBUTING.md](CONTRIBUTING.md); for the system design
and module map, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

memrelay --help                # CLI (all subcommands work except `forget`, still a stub)
memrelay config                # print the resolved configuration

ruff check . && ruff format --check .             # lint
pytest                                            # tests (incl. the walking skeleton)

python scripts/ingest_fixture.py                  # replay the redacted Copilot fixture
                                                  #   -> SessionEvents, no Graphiti
```

The **E0 de-risking spike** (does a real Copilot trace parse cleanly through TraceForge?)
is written up in [docs/e0-spike.md](docs/e0-spike.md) — verdict **GO**, with the exact
traceforge 0.1.0 API used and the deltas from `SPEC.md`.

## Status

🚧 **Pre-1.0 and under active development** — the core is functional but still being
assembled epic by epic, and the package is unpublished (v0.0.1). What works today:

- **CLI** — `init`, `start`, `stop`, `status`, `observe`, `seed`, `guidance`, `mcp`, and `config`
  are all implemented (`forget` remains a stub).
- **Engine (E4)** — a config-driven Graphiti wrapper over an embedded Kuzu database,
  with local fastembed embeddings and the `borrow-host` / `byo-key` LLM strategies.
- **Daemon (E6/E7)** — a background process that owns the Kuzu engine, hosts the
  spool → Graphiti ingester, and answers `search` / `detail` / `note` / `health` over
  a local socket.
- **MCP server (E6/E7)** — a stdio server exposing `memory_recall`, `memory_detail`,
  and `memory_note` to any MCP-capable agent.
- **Copilot ingestion** — Copilot session → `SessionEvent` normalization into a durable
  SQLite spool; `memrelay observe` replays a discovered session through the pipeline.
- **Provider framework (E12)** — the `AgentProvider` seam plus a registry with
  auto-detection. **Twelve coding agents ship:** Copilot and Claude Code (reference
  providers, borrow-host LLM) plus Codex, Cursor/Continue, Cline, Aider, Amazon Q, Goose,
  OpenCode, OpenHands, SWE-agent, and Antigravity
  ([#71](https://github.com/dfinson/memrelay/issues/71), byo-key LLM). memrelay serves its
  MCP server to the JSON-registry agents (Copilot, Claude, Cline, Amazon Q, OpenCode) and
  ingests the rest. See [SPEC.md](SPEC.md) for the coverage matrix.

Still early: the `local` LLM strategy ([#64](https://github.com/dfinson/memrelay/issues/64))
and the framework-runtime providers (CrewAI, LangGraph, OpenAI Agents, …) are planned. See
[SPEC.md](SPEC.md) for the full plan and [docs/e0-spike.md](docs/e0-spike.md) for the
original Copilot ingestion spike.

## License

MIT
