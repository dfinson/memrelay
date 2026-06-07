# memrelay

Persistent memory for GitHub Copilot CLI — powered by [Graphiti](https://github.com/getzep/graphiti).

memrelay automatically observes your Copilot CLI sessions, extracts knowledge into a local graph, and surfaces relevant memories before each interaction. No memory files. No manual summaries. No graph terminology. Just `copilot` with memory that works.

## What it does

**Automatically observes:**
- User prompts and assistant responses
- Tool calls and command executions
- File reads and writes
- Git commits and pull request discussions

**Automatically retrieves:**
- Relevant context injected before each Copilot interaction
- Cross-session continuity (remembers what you worked on yesterday)
- Cross-repo knowledge (patterns from one project inform another)

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
memrelay init      # creates ~/.memrelay/, registers MCP server with Copilot CLI
memrelay start     # starts the background daemon
```

Then just use Copilot normally:

```bash
copilot
```

Memory is automatic. The daemon observes sessions in the background and the MCP server provides memory tools to the agent.

## Zero configuration

The default stack requires **zero API keys**:

| Component | Default | What it does |
| --- | --- | --- |
| Graph database | Kuzu (embedded, local file) | Stores the knowledge graph |
| LLM | Copilot CLI (your existing subscription) | Entity extraction, summarization |
| Embeddings | fastembed (ONNX, CPU, ~67MB) | Semantic similarity for retrieval |

Everything runs locally. No Docker, no Neo4j, no cloud services.

## Architecture

```
┌─────────────────┐       ┌──────────────────────┐
│  Copilot CLI     │       │  memrelay daemon     │
│  (agent process) │       │  (background)        │
│                  │       │                      │
│  ┌────────────┐  │       │  Session watcher     │
│  │ MCP Server │──┼─sock──│  → tracemill pipeline│
│  │ (tools)    │  │       │  → Graphiti ingestion │
│  └────────────┘  │       │  → Kuzu graph DB     │
└─────────────────┘       └──────────────────────┘
```

**Two processes:**
- **Daemon** — persistent background process that owns the Kuzu database and Graphiti instance. Watches session files, ingests events, answers queries via Unix socket.
- **MCP server** — spawned by Copilot CLI as a stdio subprocess. Thin client that forwards tool calls to the daemon.

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
provider = "copilot"    # uses your Copilot subscription, no API key

[embeddings]
provider = "local"
model = "BAAI/bge-small-en-v1.5"
```

**Override with direct API keys** (faster inference, native structured output):

```toml
[llm]
provider = "openai"
api_key_env = "OPENAI_API_KEY"
model = "gpt-4o-mini"
```

## Dependencies

memrelay depends on [tracemill](https://github.com/dfinson/tracemill) for event parsing and normalization. tracemill handles the pipeline; memrelay handles memory.

| Dependency | Purpose |
| --- | --- |
| `tracemill` | Event normalization pipeline |
| `graphiti-core` | Knowledge graph engine |
| `kuzu` | Embedded graph database |
| `fastembed` | Local ONNX embeddings |
| `mcp` | Model Context Protocol server |

## How it works

1. **Observe** — The daemon watches `~/.copilot/sessions/` for new session files and tails them for events
2. **Normalize** — Events pass through a tracemill pipeline (parse → enrich → filter)
3. **Ingest** — Filtered events are written to a durable SQLite spool, then batch-ingested into Graphiti
4. **Extract** — Graphiti extracts entities, relationships, facts, and temporal information
5. **Retrieve** — When the agent calls `memory_recall`, the MCP server queries the daemon, which searches Graphiti using hybrid semantic + graph search
6. **Format** — Results are formatted as structured markdown and injected into the agent's context

## Design principles

- **Zero config** — works with `pip install` + `memrelay init`
- **Local-first** — everything runs on your machine, no cloud required
- **Invisible** — when working correctly, you never think about memory
- **Thin layer** — memrelay is integration glue around Graphiti, not a replacement

## Status

🚧 **Under development** — not yet functional. See [SPEC.md](SPEC.md) for the full implementation plan.

## License

MIT
