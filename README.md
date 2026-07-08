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

## Zero configuration

The default stack requires **zero API keys**:

| Component | Default | What it does |
| --- | --- | --- |
| Graph database | Kuzu (embedded, local file) | Stores the knowledge graph |
| LLM | borrow-host — reuse an agent's own model (e.g. your Copilot subscription) | Entity extraction, summarization |
| Embeddings | fastembed (ONNX, CPU, ~67MB) | Semantic similarity for retrieval |

Everything runs locally. No Docker, no Neo4j, no cloud services. Prefer your own key or a fully local model? Switch the LLM strategy to `byo-key` or `local` (see Configuration).

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
memrelay forget --repo owner/name    # Delete memories for a repo
memrelay forget --namespace name     # Delete entire namespace
memrelay seed                        # Bootstrap memory from git history
memrelay config                      # Show current config
```

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
```

**Override the LLM strategy** — `byo-key` for direct API keys (faster inference, native structured output) or `local` for a fully offline model:

```toml
[llm]
strategy = "byo-key"
provider = "openai"
api_key_env = "OPENAI_API_KEY"
model = "gpt-4o-mini"
```

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

1. **Observe** — Each agent provider discovers active sessions (Copilot reads `~/.copilot/session-store.db`; other agents read their own store or logs) and the daemon tails them for events
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

Requires Python 3.11–3.13.

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

memrelay --help                # CLI (subcommands are E0 stubs except `config`)
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

🚧 **Under development** — not yet functional. **E0 (foundations + Copilot ingestion
spike) landed:** package skeleton, config, CLI surface, CI, and a verified
Copilot → `SessionEvent` walking skeleton. See [SPEC.md](SPEC.md) for the full plan and
[docs/e0-spike.md](docs/e0-spike.md) for the spike report.

## License

MIT
