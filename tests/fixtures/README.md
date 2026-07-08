# Test fixtures

## `copilot_session.jsonl`

A **redacted, minimal** excerpt of one real GitHub Copilot CLI session
(`~/.copilot/session-state/<id>/events.jsonl`), used by the E0 walking-skeleton
tests to prove the Copilot → traceforge → `SessionEvent` path end-to-end without
touching a live machine store. CI is therefore hermetic — it never reads `~/.copilot`.

- **14 records** — a coherent one-turn mini-session trimmed to **exactly one event per
  mapped kind** the tests need to exercise. Spanning 14 wire event types:
  `session.start`, `system.message`, `user.message`, `assistant.turn_start`,
  `assistant.message`, `tool.execution_start`, `permission.requested`,
  `permission.completed`, `hook.start`, `hook.end`, `tool.execution_complete`,
  `session.workspace_file_changed`, `assistant.turn_end`, `session.shutdown`.
- Maps (via traceforge's packaged `copilot.yaml`) to **14 `SessionEvent`s**, one per kind:
  `session.started`, `message.system`, `message.user`, `turn.started`,
  `message.assistant`, `tool.call.started`, `tool.call.completed`,
  `permission.requested`, `permission.granted`, `hook.started`, `hook.completed`,
  **`file.edited`**, `turn.ended`, `session.ended`.
- Through a lean `EventPipeline` **13 are delivered** (the single `tool.call.started`
  is coalesced into its `tool.call.completed` pair by the enricher).

> The full 103-record de-risking measurement (real session → 103 `SessionEvent`s → 90
> delivered) lives in [`docs/e0-spike.md`](../../docs/e0-spike.md). This committed fixture
> is the **minimal** subset that keeps CI fast and deterministic while still covering every
> kind the walking-skeleton asserts on.

### Composition notes

- **Pair matching.** Start/complete and start/end pairs are selected so they share their
  linking id (`toolCallId`, `turnId`, `hookInvocationId`) — a naive first-of-each-type pick
  would mismatch them, because the session's first `tool.execution_complete` belonged to a
  different call than its first `tool.execution_start`.
- **`file.edited` exemplar.** The chosen reference session performed no file writes, so a
  single **synthetic** `session.workspace_file_changed` record (real wire shape, fully
  `[redacted]` path, `operation: edit`) is injected to cover the `file.edited` mapping.
  Every other record is a redacted real record.

### Redaction

Produced by [`scripts/capture_fixture.py`](../../scripts/capture_fixture.py), which is
**structure-preserving**: it keeps every field the mapping reads (the `type`
discriminator, `timestamp`, enum values, model/tool names, numbers, booleans, and id
linkage) but replaces all free text with `[redacted]` and remaps every id to a
deterministic placeholder (so `parentId → id` links survive de-identification).

Scrubbed: message/reasoning content, file paths, `cwd`/git metadata, tool arguments,
tool output, summaries, usernames. Kept (non-sensitive, needed for fidelity): event
types, ISO timestamps, model names (e.g. `claude-sonnet-4.6`), tool names
(e.g. `powershell`, `glob`), file operations (`create`/`edit`/`delete`),
`copilotVersion`, hook types, and aggregate numeric telemetry.

A value-level scan confirms the committed fixture contains **no** free text, filesystem
paths, usernames, secrets, or real UUIDs — only `[redacted]`, placeholder ids
(`00000000-…`), ISO timestamps, and the structural enums above.

The capture script **self-verifies**: it replays the source records and the redacted
output through the real adapter and asserts the produced `SessionEvent` **kind
histogram is identical** — redaction must never change how the trace maps.

### Regenerating

```bash
# Minimal fixture (what is committed): one event per required kind, redacted.
python scripts/capture_fixture.py --session-id <a-real-session-id>

# Auto-pick a type-rich session:
python scripts/capture_fixture.py

# Full redacted session (every record) instead of the minimal excerpt:
python scripts/capture_fixture.py --session-id <id> --full
```
