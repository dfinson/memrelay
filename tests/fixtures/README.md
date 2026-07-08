# Test fixtures

## `copilot_session.jsonl`

A **redacted** capture of one real GitHub Copilot CLI session
(`~/.copilot/session-state/<id>/events.jsonl`), used by the E0 walking-skeleton
tests to prove the Copilot → traceforge → `SessionEvent` path end-to-end without
touching a live machine store.

- **103 records**, spanning 13 wire event types: `session.start`, `session.shutdown`,
  `user.message`, `system.message`, `assistant.message`, `assistant.turn_start/end`,
  `tool.execution_start/complete`, `permission.requested/completed`, `hook.start/end`.
- Maps (via traceforge's packaged `copilot.yaml`) to **103 `SessionEvent`s** across 13
  kinds; through a lean `EventPipeline` **90 are delivered** (the 13 `tool.call.started`
  events are coalesced into their `tool.call.completed` pairs by the enricher).

### Redaction

Produced by [`scripts/capture_fixture.py`](../../scripts/capture_fixture.py), which is
**structure-preserving**: it keeps every field the mapping reads (the `type`
discriminator, `timestamp`, enum values, model/tool names, numbers, booleans, and id
linkage) but replaces all free text with `[redacted]` and remaps every id to a
deterministic placeholder (so `parentId → id` links survive de-identification).

Scrubbed: message/reasoning content, file paths, `cwd`/git metadata, tool arguments,
tool output, summaries, usernames. Kept (non-sensitive, needed for fidelity): event
types, ISO timestamps, model names (e.g. `claude-sonnet-4.6`), tool names
(e.g. `view`, `grep`), `copilotVersion`, hook types.

The capture script **self-verifies**: it replays both the original and the redacted
files through the real adapter and asserts the produced `SessionEvent` **kind
histogram is identical** — redaction must never change how the trace maps.

### Regenerating

```bash
python scripts/capture_fixture.py --session-id <a-real-session-id>
# or auto-pick a type-rich session:
python scripts/capture_fixture.py
```
