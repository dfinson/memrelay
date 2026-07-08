# E6/E7 Walking Skeleton — De-Risk Findings

De-risk pass for the Observation Daemon (E6) + MCP Server (E7) walking skeleton, in
the same spirit as `docs/e0-spike.md`. Everything below was verified **by inspection
of the installed packages and the real machine**, not from SPEC.md (which is
illustrative). Deltas from SPEC are called out explicitly.

## Verdict

**GREEN — proceed.** The MCP SDK, the async IPC transport, and the Copilot
registration file were all verified live. Two material deltas from SPEC were found
(MCP config `type`, and per-OS IPC on Windows); both are handled below. No blocker.

## 1. MCP SDK API (`mcp`)

- **Installed locally: `mcp 1.25.0`** (not `1.28.1` as briefed). CI installs whatever
  `mcp>=1.0` resolves to at build time. We therefore target only the **stable FastMCP
  subset** that is identical across 1.25–1.28: `FastMCP(...)`, the `@server.tool()`
  decorator, `run_stdio_async()` / `run(transport="stdio")`, and `call_tool()`.
- **Stand-up approach: `mcp.server.fastmcp.FastMCP`.** SPEC §4.1's `@mcp_server.tool()`
  decorator is **accurate** for FastMCP — no need for the low-level
  `mcp.server.lowlevel.Server` + `stdio_server()` path.
  - `FastMCP.tool(...)` is a decorator factory: `@mcp.tool()` over an annotated async
    function registers a tool; the JSON schema is derived from type hints.
  - `FastMCP.run_stdio_async()` runs the stdio server (async); `run("stdio")` is the
    sync wrapper used by `memrelay mcp`.
- **In-process testability (matters for the hermetic gate test):**
  `await FastMCP.call_tool(name, arguments)` invokes a tool without spawning a
  subprocess.
  - **Nuance:** in 1.25 `call_tool` returns a **2-tuple** `([TextContent(...)], {"result": <str>})`,
    even though the annotation says `Sequence[ContentBlock] | dict`. Tests normalize
    both shapes via a small helper (extract `.text` from the first content block, or
    read `["result"]`).

## 2. IPC transport (asyncio), per-OS

Probed on this machine (`sys.platform == "win32"`):

| capability | Windows (here) | Linux/macOS (CI) |
| --- | --- | --- |
| `asyncio.start_unix_server` | **absent** | present |
| `socket.AF_UNIX` | **absent** | present |
| `asyncio.start_server` (TCP) | present | present |

- **POSIX (this is what CI exercises):** Unix domain socket via
  `asyncio.start_unix_server` at `~/.memrelay/daemon.sock`. This is the primary,
  spec-aligned path and the one Linux py3.11/3.12/3.13 runs.
- **Windows — DELTA from SPEC §2 ("named pipe"):** asyncio has no Unix-socket support
  and named-pipe asyncio is heavyweight for a skeleton. We use the manager-approved
  **127.0.0.1 loopback TCP fallback**: the daemon binds `127.0.0.1:0`, writes the
  chosen port to `~/.memrelay/daemon.port`, and the client reads that file to connect.
  Bound to loopback only.
- **Framing:** newline-delimited JSON (`{...}\n` per message). Verified round-trip on
  loopback. One request → one response line; connection closable per request.

## 3. Copilot MCP registration — DELTA from SPEC §2

Inspected the **real** `~/.copilot/mcp-config.json` (read-only). Shape confirmed:
top-level `{"mcpServers": { <name>: <entry> }}`, merged with the built-in `github`
server and others.

**Material delta:** every stdio/subprocess server in the live file uses
**`"type": "local"`** (with `command` + `args` + `tools`), e.g. `github`, `dbhub`,
`azure`. SPEC §2 shows `"type": "stdio"`. A separate `"type": "http"` exists for URL
servers (`codeplane`). **There is no `"type": "stdio"` entry in the installed CLI.**

→ We therefore write the empirically-correct entry Copilot actually understands:

```json
{
  "mcpServers": {
    "memrelay": {
      "type": "local",
      "command": "memrelay",
      "args": ["mcp"],
      "tools": ["*"],
      "env": {}
    }
  }
}
```

`memrelay init` **merges** this into any existing `mcpServers` (preserving `github`
et al.) and is **idempotent** (re-running is a no-op). The entry shape lives behind
the Copilot provider's `register()` seam so other agents can plug in later.

## 4. Environment / tooling gotchas (local dev loop)

- **memrelay not installed & `pip install -e .` fails locally** on writing
  `C:\Python312\Scripts\memrelay.exe` (a Windows Scripts-dir `.deleteme` race). Not a
  memrelay problem and not needed for tests. Local loop uses **`PYTHONPATH=src`**;
  CI's `pip install -e ".[dev]"` is unaffected (Linux).
- **`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`** required locally: a stray global `langsmith`
  pytest plugin crashes on import (missing `requests`). CI only installs
  `memrelay[dev]`, so it does not see this plugin.
- **No `pytest-asyncio`** in `[dev]` — so **async tests must use `asyncio.run(...)`
  wrappers**, never `@pytest.mark.asyncio`. (The E0 walking-skeleton test already
  follows this convention.)
- **`ruff format --check .`** runs in CI — formatting is enforced, not just linting.
- Baseline before this work: **31 passed**, `ruff check` + `ruff format --check` clean.

## 5. Single-writer invariant (E6-S2)

Baked in structurally: `src/memrelay/mcp/**` never imports `kuzu`, `graphiti`, or
`memrelay.engine` — it only reaches the graph via `DaemonClient` over the socket. The
daemon is the sole owner of graph state (today: `StubBackend`; later: the injected E4
`MemoryEngine`). A structural test asserts the no-import rule so the boundary can't
silently erode.

## 6. Shared backend contract (with the parallel E4 session)

The daemon accepts any object implementing this `Protocol` (async), defaulted to
`StubBackend` now; E4's `MemoryEngine` will implement it verbatim for a one-line swap:

```python
async def search(self, query: str, namespace: str, prefer_repo: str | None = None) -> dict
async def detail(self, node_uuid: str, namespace: str) -> dict
async def note(self, content: str, namespace: str, repo: str | None = None) -> str
async def health(self) -> dict
```

Return payloads are plain JSON-serializable dict/str so they cross the socket
unchanged (search → `{"nodes","edges","scores"}`; detail →
`{"node","connected_edges","episodes"}`; note → a status string; health →
`{"status","sessions_observed","episodes_ingested","spool_pending"}`).
