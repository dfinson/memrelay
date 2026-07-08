# E0 — Foundations & De-risking Spike Report

**Verdict: GO.** The riskiest assumption behind memrelay — that GitHub Copilot CLI's
real on-disk session trace parses cleanly, end-to-end, through traceforge into
`SessionEvent`s — holds. A real 103-record Copilot session normalizes to 103
`SessionEvent`s via traceforge's packaged `copilot.yaml` mapping and flows through a
real `EventPipeline` to a sink, with high fidelity and no fatal surprises.

The architecture in `SPEC.md` is sound. However, the **installed traceforge
(`traceforge-toolkit==0.1.0`) differs from the SPEC's illustrative code in seven
concrete places.** None is fatal; all are documented below with the real API. Per the
spike's hard rule, memrelay is written against the *actually installed* API — no
fictional traceforge symbols were stubbed to make anything compile.

---

## 1. What was verified, and how

- **Environment:** Windows, CPython 3.12.10 in a clean venv. `pip install -e ".[dev]"`
  resolved and installed cleanly — all seven SPEC runtime deps plus `memrelay 0.0.1`
  (`traceforge-toolkit 0.1.0`, `graphiti-core 0.29.2`, `kuzu 0.11.3`, `fastembed 0.8.0`,
  `mcp 1.28.1`, `click 8.4.2`, `structlog 26.1.0`), no dependency conflicts.
- **Real data is present on this machine:** `~/.copilot/session-store.db` (128 MB;
  `turns` table = 5219 rows) **and** thousands of
  `~/.copilot/session-state/<id>/events.jsonl` files. There is **no missing-DB blocker**.
- **Reference session:** `035e0daa-…` — a real 103-record session covering the full
  lifecycle (session start/shutdown, user/assistant/system messages, turns, tool calls,
  permissions, hooks). It is the **de-risking measurement** in §6a.
- **Committed fixture:** a **redacted, minimal 14-record excerpt** of that session —
  exactly one event per mapped kind the tests need, **including `file.edited`** (injected
  as a synthetic `session.workspace_file_changed` record, since the reference session did
  no file writes). Captured into
  [`tests/fixtures/copilot_session.jsonl`](../tests/fixtures/copilot_session.jsonl); see
  [that folder's README](../tests/fixtures/README.md) for redaction + composition details.
- **Walking skeleton** ([`scripts/ingest_fixture.py`](../scripts/ingest_fixture.py) +
  [`tests/integration/test_walking_skeleton.py`](../tests/integration/test_walking_skeleton.py))
  runs the committed fixture through the real adapter and pipeline on every CI run —
  hermetically (tests assert against the committed fixture, never live `~/.copilot`).

---

## 2. The real traceforge API surface memrelay uses

Verified by inspecting the installed package (`inspect.signature`) — not from the SPEC:

```python
# Top-level exports actually present (traceforge/__init__.py):
from traceforge import (
    EventPipeline, Enricher, SessionEvent, StorageSink,
    MappedJsonAdapter, CallbackSink,
)
from traceforge.sources import SqliteSource
from traceforge.parsers.copilot import CopilotPreParser      # NOT traceforge.preparse

# Adapter (canonical Copilot path):
MappedJsonAdapter.from_yaml(yaml_path: str, session_id: str) -> MappedJsonAdapter
adapter.parse(raw: bytes | str) -> Iterator[SessionEvent]     # from a JSONL line
adapter.parse_dict(obj: dict) -> Iterator[SessionEvent]       # from a dict record

# Pipeline:
EventPipeline(
    sinks: list[StorageSink], enricher: Enricher | None = None,
    phase_inferencer=None, boundary_inferencer=None, title_inferencer=None,
    enable_phase: bool = True, enable_boundary: bool = True, enable_title: bool = False,
    max_sessions: int | None = 4096, governance=None, metrics=None,
)
await pipeline.push(event); await pipeline.flush(); await pipeline.close()   # all async

# Sink base:
class StorageSink:                    # abstract: on_event only
    async def on_event(self, event: SessionEvent) -> None: ...   # @abstractmethod
    async def flush(self) -> None: ...    # concrete no-op default
    async def close(self) -> None: ...    # concrete no-op default
    # + on_span / on_usage / on_title_update concrete defaults

Enricher(custom_classifications=None, config=None, config_path=None)

# Fallback (SQLite) path:
SqliteSource(path, name, table="turns", order_column="id", columns=None,
             where=None, interval=2.0, start_at="end", session_filter=None)
CopilotPreParser().parse_turn(row: dict) -> Iterator[dict]
```

The exact wiring lives in
[`src/memrelay/providers/copilot.py`](../src/memrelay/providers/copilot.py) and
[`src/memrelay/ingest/fixture_runner.py`](../src/memrelay/ingest/fixture_runner.py).

---

## 3. Real Copilot record shapes

### 3a. Canonical — `~/.copilot/session-state/<id>/events.jsonl` (primary)

One JSON object per line; `type` is the discriminator, `data.*` is camelCase:

```json
{"type": "session.start", "id": "<uuid>", "timestamp": "2026-…Z", "parentId": null,
 "data": {"sessionId": "<uuid>", "selectedModel": "claude-sonnet-4.6",
          "copilotVersion": "1.0.65", "context": {"cwd": "…", "gitRoot": "…", "branch": "…"}}}
{"type": "tool.execution_start", "id": "…", "timestamp": "…",
 "data": {"toolCallId": "…", "toolName": "view", "arguments": {…}}}
{"type": "tool.execution_complete", "id": "…", "timestamp": "…",
 "data": {"toolCallId": "…", "success": true, "result": {"content": "…"}}}
{"type": "session.workspace_file_changed", "id": "…", "timestamp": "…", "parentId": "…",
 "data": {"path": "…", "operation": "create"}}   // operation ∈ create | edit | delete
```

`copilot.yaml` maps these to kinds like `session.started`, `message.user/assistant/system`,
`turn.started/ended`, `tool.call.started/completed`, `permission.requested/granted`,
`hook.started/completed`, `file.edited`, `telemetry.usage`. Note **`file.edited` comes from
the raw `session.workspace_file_changed` type** (not from a tool call) — its `operation`
enum maps straight through. This path carries **real tool-call ids, hook ids, turn ids, and
success flags** — the high-fidelity source, and the one traceforge's own mapping header
recommends.

### 3b. Fallback — `~/.copilot/session-store.db` `turns` table

Columns: `id, session_id, turn_index, user_message, assistant_response, timestamp`.
`CopilotPreParser().parse_turn(row)` shreds one turn into per-message/-tool JSON records,
then the `copilot_markdown` mapping normalizes them. Tool calls here are **inferred
heuristically from markdown fenced blocks** — lower fidelity. On this machine the richer
`forge_trajectory_events` table exists but is **empty (0 rows)**, so the markdown path is
the only SQLite option and is strictly a fallback.

---

## 4. Deltas — where reality differs from `SPEC.md`

> These are the important spike output. Each was verified against the installed package.
> memrelay implements the **Real** column.

| # | Topic | SPEC.md (§3.2 illustrative) | Reality (traceforge 0.1.0) |
| - | ----- | --------------------------- | -------------------------- |
| 1 | PreParser import | `from traceforge.preparse import CopilotPreParser` | `from traceforge.parsers.copilot import CopilotPreParser` — there is **no** `traceforge.preparse` module |
| 2 | PreParser call | `pre.parse(raw)` | `CopilotPreParser().parse_turn(row: dict)` — takes a **turns row dict**; no generic `.parse()`. The old process-log `parse_log_line` path was removed upstream (traceforge #45) |
| 3 | Mapping resolution | `MappedJsonAdapter.from_yaml("copilot_markdown", session_id=…)` (a *name*) | `from_yaml(yaml_path, session_id)` needs a **filesystem path**; `traceforge.mappings` ships no name→path resolver, so resolve via `importlib.resources.files("traceforge.mappings")/"<name>.yaml"` |
| 4 | Feeding records | `adapter.parse(record)` on a preparser **dict** | `parse()` takes a JSON **str/bytes**; feed a dict with `adapter.parse_dict(dict)`. (For the canonical path we feed raw JSONL lines straight to `parse()`.) |
| 5 | `SqliteSource` | `SqliteSource(path=…)`, then sync `source.read(session_ref)` with a `SessionRef` | Requires a positional **`name`**; is an **async** iterator / async context manager (`async with` / `async for`) — there is **no** sync `read()` and **no** `SessionRef` type. Also defaults `start_at="end"` (new rows only) and `table="turns"`, and does **not** expand `~` |
| 6 | Which source is canonical | Leads with SQLite + `CopilotPreParser` + `copilot_markdown` | traceforge's own parser/mapping headers call SQLite+markdown a *"thin fallback"* and name `events.jsonl` + `copilot.yaml` the canonical, high-fidelity path — confirmed by the fixture (1:1, 103→103, real tool ids) |
| 7 | Pipeline ML flags | `EventPipeline(sinks=…, enricher=…, governance=None)` | Same call works, but the constructor also defaults `enable_phase=True` / `enable_boundary=True`, which lazy-load packaged ONNX bundles. E0 passes `enable_phase=False` / `enable_boundary=False` for a lean, deterministic, offline transport pipeline |

Plus one behavioral finding worth recording:

- **Unmapped-but-valid records become `raw`, they are not dropped.** `adapter.parse` only
  drops **malformed JSON** (logged, never raised). A structurally valid record whose
  `type` isn't in the mapping falls through to the mapping's `default_kind: raw`
  (e.g. `session.mode_changed` → `raw`). memrelay's filtering must therefore expect
  `raw` events, not assume unknown inputs vanish.

### Consequences for the SPEC

`SPEC.md §3.2`'s code block should be updated to the real imports/calls (deltas 1–5),
and §2.1/§3 should state the canonical Copilot source is **file-watch over
`events.jsonl` + `copilot.yaml`**, with SQLite+markdown as the documented fallback
(delta 6). §3.3's `EventPipeline(...)` should note the `enable_phase`/`enable_boundary`
flags (delta 7). These are doc-level corrections; the *design* is unaffected.

---

## 5. SPEC claims that are confirmed correct

- Visibility lives at **`event.metadata.visibility`**; the enum is exactly
  `visible | system | collapsed` (a `StrEnum` in `traceforge.classify.workflow`).
- `adapter.parse()` is a **sync generator contracted never to raise** (bad input is
  dropped and logged) — verified with malformed input in the test suite.
- `pipeline.push()` / `flush()` / `close()` are **async**; `StorageSink` exposes async
  `on_event` (abstract) + `flush` / `close` (concrete).
- Top-level exports include `EventPipeline`, `Enricher`, `SessionEvent`, `StorageSink`
  (also `MappedJsonAdapter`, `CallbackSink`).
- There is **no** `CLIJsonlAdapter`; the base is a `MappedJsonAdapter` driven by YAML.
- Governance is opt-out via **`EventPipeline(governance=None)`**.

---

## 6. Walking-skeleton evidence

Two measurements, both with `enable_phase=False`, `enable_boundary=False`,
`governance=None`, `enricher=Enricher()`.

### 6a. Full reference session (de-risking measurement)

Replaying the real 103-record session `035e0daa-…`:

```
parsed:    103 SessionEvent(s) from adapter        (1:1 with the 103 JSONL records)
delivered:  90 to sink (after pipeline enrich/filter)
elapsed:   ~15 ms
by kind (delivered):  permission.requested 13, permission.granted 13, hook.started 13,
                      hook.completed 13, tool.call.completed 13, turn.started 7,
                      message.assistant 7, turn.ended 7, session.started 1,
                      message.system 1, message.user 1, session.ended 1
by visibility:        visible 88, system 2
```

### 6b. Committed hermetic fixture (what CI asserts)

The committed [`copilot_session.jsonl`](../tests/fixtures/copilot_session.jsonl) is the
minimal 14-record excerpt — one event per mapped kind, **including `file.edited`**:

```
parsed:    14 SessionEvent(s) from adapter        (one per mapped kind)
delivered: 13 to sink                             (the 1 tool.call.started is coalesced)
by kind (delivered):  file.edited 1, hook.started 1, hook.completed 1,
                      message.system 1, message.user 1, message.assistant 1,
                      permission.requested 1, permission.granted 1,
                      tool.call.completed 1, turn.started 1, turn.ended 1,
                      session.started 1, session.ended 1
by visibility:        visible 11, system 2
```

Two enrichment behaviors are demonstrated and asserted in tests:

- **Tool pairing:** `tool.call.started` events are coalesced into their
  `tool.call.completed` partners (103→90 for the full session; 14→13 for the committed
  fixture), matching SPEC §3.3's "tool pairing, duration".
- **Visibility classification:** the `Enricher` reclassifies events to `system`
  visibility (2 in each measurement), exactly the signal `GraphitiSink` will later filter
  on (SPEC §3.4).

> Note: in a full install, `requests` is present transitively (via `litellm`/
> `graphiti-core`), so `enable_phase=True` would attempt to load the ONNX phase model
> rather than degrade. E0 keeps it off deliberately for determinism and offline CI.

---

## 7. Go / No-Go

**GO.** Recommendations carried into later epics:

1. Implement the Copilot provider's **primary** source as file-watch over
   `events.jsonl` + `copilot.yaml`; keep SQLite + `CopilotPreParser` + `copilot_markdown`
   as a documented fallback (both are wired in `providers/copilot.py`).
2. `GraphitiSink` should subclass `StorageSink` (async `on_event`/`flush`/`close`) and
   filter on `event.metadata.visibility`, treating `raw`/`system` as skip/summarize.
3. Turn ML enrichment (`enable_phase`/`enable_boundary`) on only when its inputs are
   guaranteed present; it degrades gracefully but adds latency.
4. File a small docs PR (or upstream note) to reconcile `SPEC.md §3.2` with the real
   traceforge 0.1.0 API (deltas 1–7).
