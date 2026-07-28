# memrelay live end-to-end smoke (real borrow-host LLM)

This runbook drives the **full first-time-user happy path** of memrelay from a clean
from-source checkout, end to end, with a **real host-agent LLM** doing entity extraction —
the one path CI cannot cover. Every integration test swaps in a deterministic
`MockLLMClient` for extraction, and `scripts/first_run_smoke.py` proves the key-less path
but *deliberately skips* `memory_note` (which needs live extraction). This smoke picks up
exactly there.

It is reproducible by a stranger: every command below has an **expected output**, and the
current **known walls** (with root cause + evidence) are documented so you know what you
should and should not see today.

- **Automated driver:** [`scripts/smoke_e2e.py`](../scripts/smoke_e2e.py)
- **Validated against:** `origin/main` @ `70dfbe7` (live evidence captured at `5496e2d`;
  every intervening commit leaves the extraction path untouched —
  `git diff 5496e2d..70dfbe7 -- src/memrelay/engine/llm/borrow_host.py` is empty, so
  `borrow_host.py` is byte-identical and the wall reproduces unchanged at the current tip).
- **Captured on:** Windows 11, Python 3.12, authenticated `copilot` CLI on PATH.

---

## ⚠️ Current status (read this first)

As of `70dfbe7`, the real-LLM path **walls**: the daemon reaches the borrow-host LLM call
but the borrow-host client mis-invokes the host CLI, so **entity extraction never
succeeds**. Concretely, a first-time user today sees:

- `memrelay init`, `start`, `status`, and the MCP tool surface all **work**.
- `memory_recall` (empty) round-trips with **no keys and no LLM** — the key-less promise holds.
- `memory_note "<fact>"` returns an **error** (`HostProcessError`), and `memrelay seed`
  drains the spool but leaves `episodes_ingested: 0` — **seeded facts do not round-trip**.
- `memrelay status` shows the daemon **running** with `episodes_ingested: 0` and no error —
  so the failure is **silent** unless you call `memory_note` or inspect the daemon log.

See the **Known walls** section for root cause, evidence, and what is needed to
cross each wall. **This is the honest expected state; do not treat an empty recall after
seeding as your own misconfiguration.**

---

## Prerequisites

| Requirement | Why | Notes |
|---|---|---|
| Python `>=3.11,<3.14` | runtime | 3.12 used here |
| `git` | `memrelay seed` reads `git log` | |
| Network egress | `init` downloads the embedding model + Ladybug FTS extension | first run only, then cached per `MEMRELAY_HOME` |
| **A host agent CLI on PATH, authenticated** | **the borrow-host prerequisite** — the daemon shells out to it to extract entities | `copilot` **or** `claude`. Without one, the real-LLM path cannot run at all (Phase 2 is skipped). |
| Rust/Cargo **only if** pip resolves `litellm>=1.92` | that version is sdist-only and builds native code | avoided by the `litellm<1.92` constraint below — see **Wall B** |

> The host agent is "borrowed" as the extraction LLM instead of requiring your own API key.
> The smoke scrubs every `*_API_KEY` from the environment (and asserts they are absent), so
> a green result can only come from the host agent — never from an inherited key.

---

## The fast path: run the automated driver

```powershell
# from a fresh venv that has memrelay installed (see "Manual runbook" step 0)
python scripts/smoke_e2e.py            # full path: Phase 1 (no-LLM) + Phase 2 (real LLM)
python scripts/smoke_e2e.py --phase1-only   # CI-safe subset; no host agent required
```

**Exit codes (unambiguous):**

| Code | Meaning |
|---|---|
| `0` | Phase 1 passed AND the real-LLM canary round-tripped (full green). `--phase1-only` returns 0 when Phase 1 passes. |
| `1` | **Phase 1 failed** — a real key-less regression, or the daemon never became healthy. |
| `2` | **Phase 2 not attempted** — no `copilot`/`claude` on PATH (prerequisite missing). |
| `3` | **Phase 2 walled** — extraction failed or the canary did not round-trip. **This is today's expected result** at `70dfbe7`. |

The driver never mocks anything and never forces green. When the borrow-host fix lands it
flips from exit `3` to exit `0` and becomes the regression guard for the real-LLM path.

---

## Manual runbook

Every step lists the exact command and the **expected output**. Commands are PowerShell;
bash differs only in how you set environment variables.

### Step 0 — clean-env install from source (fresh venv)

Do **not** `pip install -e` into a shared environment. Build a throwaway venv so the smoke
mirrors a first-time user and cannot disturb other checkouts.

```powershell
git clone https://github.com/dfinson/memrelay
cd memrelay
python -m venv .smoke-venv
.\.smoke-venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

# See Wall B: pin litellm to a wheel build so the install does not require Rust.
'litellm<1.92' | Out-File -Encoding ascii constraints.txt
$env:PIP_CONSTRAINT = "constraints.txt"
pip install .
```

**Expected:** the install completes (it unpacks large wheels — `litellm`, `fastembed`,
`graphiti-core`, `ladybug` — so allow several minutes), and:

```powershell
memrelay --version      # -> memrelay, version 0.1.0
python -c "import litellm, fastembed, graphiti_core, ladybug; print('deps ok')"
```

### Step 1 — `memrelay init` (zero-config)

```powershell
# Optional: pin a hermetic home so this smoke leaves nothing behind.
$env:MEMRELAY_HOME = "$PWD\.smoke-home"
memrelay init
```

**Expected:** exit `0`; it writes `config.toml`, registers the MCP server
(`mcp-config.json`), and **downloads the embedding model** on first run (network-bound).
Re-running `init` is idempotent (the model is not re-downloaded). On Windows you may see a
benign HuggingFace symlink warning (needs Developer Mode) — harmless.

```powershell
Test-Path "$env:MEMRELAY_HOME\config.toml"           # -> True
Get-ChildItem "$env:MEMRELAY_HOME\models"            # -> a populated model cache
```

### Step 2 — `memrelay start` (daemon up)

```powershell
memrelay start
```

**Expected (first run):** the command frequently prints
`Error: daemon did not become healthy within the timeout` and returns a **non-zero exit
code** — even though the start is succeeding. This is **Wall C**:
the CLI waits only 10 s, but the detached daemon needs longer to build the engine. **Do not
stop here.** Poll `status` until it reports running (below).

### Step 3 — `memrelay status` (health)

```powershell
# poll — the daemon becomes healthy shortly after `start` returns
for ($i=0; $i -lt 60; $i++) { if ((memrelay status) -match 'memrelay daemon: running') { break }; Start-Sleep 3 }
memrelay status
```

**Expected (healthy):**

```
memrelay daemon: running
  pid:               <n>
  sessions_observed: 0
  episodes_ingested: 0
  spool_pending:     0
```

> If `status` keeps reporting `memrelay daemon: not running` for minutes, see
> **Wall D**: on a
> loaded machine the detached daemon can die during startup, and because its output goes to
> `DEVNULL` there is no log to explain why.

### Step 4 — the agent has memory tools; empty recall round-trips (no LLM)

The three MCP tools (`memory_recall`, `memory_detail`, `memory_note`) are what a host agent
calls. To exercise them without an agent, use the bundled driver (which does exactly this)
or a short `mcp`-client snippet:

```powershell
python scripts/smoke_e2e.py --phase1-only
```

**Expected:** `PHASE 1 PASSED`. Internally: the tool list is exactly
`{memory_recall, memory_detail, memory_note}`, and `memory_recall` returns
`No relevant memories found.` **without error** — proving agent → MCP → daemon → real engine
→ Ladybug + local embedder works with no keys and no LLM.

### Step 5 — seed real content and trigger real-LLM extraction

Two equivalent ways to drive the real borrow-host extraction; both go through the daemon's
borrow-host client:

```powershell
# (a) seed git history -> spool -> the daemon's ingester drains it into memory
memrelay seed --path . --max-count 3
```

**Expected output of `seed`:**

```
seeded git history from <path>
  namespace: <ns>
  repo:      <repo>
  commits:   3
  spool:     <MEMRELAY_HOME>\spool\spool.db
```

```powershell
# (b) store one fact explicitly (synchronous: extraction runs inline)
python scripts/smoke_e2e.py       # its Phase 2 calls memory_note("<canary>") then recall
```

**Expected TODAY (`70dfbe7`) — the wall:**

- `seed` reports `commits: 3` and the spool drains (`spool_pending` returns to `0`), but
  `memrelay status` keeps `episodes_ingested: 0`. Extraction failed for every episode; the
  ingester retries each up to 5× then drops it as poison. **No error is surfaced to `status`.**
- `scripts/smoke_e2e.py` reaches `memory_note`, which returns **`isError=True`** with a
  `HostProcessError`, and prints a `WALLED AT REAL-LLM EXTRACTION` banner, exit code **3**.

**Expected once the borrow-host bug is fixed:** `episodes_ingested` climbs above 0 after
seeding, and the driver's canary round-trips (exit `0`).

### Step 6 — recall the seeded facts (round-trip)

```powershell
python scripts/smoke_e2e.py     # Phase 2 recalls the canary it just stored
```

**Expected TODAY:** empty — the canary does **not** round-trip because extraction never
completed (`WALLED`, exit 3). **Expected after the fix:** `memory_recall` surfaces the
stored fact.

### Step 7 — stop / teardown

```powershell
memrelay stop
memrelay status        # -> memrelay daemon: not running
Remove-Item -Recurse -Force $env:MEMRELAY_HOME   # if you used a throwaway home
```

**Expected:** `stop` prints `memrelay daemon stopped.`; `status` then reports not running.

---

## Known walls (`70dfbe7`)

### Wall A — borrow-host mis-invokes the host CLI, so extraction never succeeds

**This is the headline wall.** It is a real product bug, not an environment issue, and the
test-suite mock has hidden it entirely.

**Root cause (all OSes):** `CopilotHostProcess`/`ClaudeHostProcess` in
`src/memrelay/engine/llm/borrow_host.py` run the host CLI with `-p` and send the prompt on
**stdin** (`_complete_via_subprocess`, `borrow_host.py:137–162` — the launch is `:148`, the
stdin write is `process.communicate(...)` at `:155`; `CopilotHostProcess` sets
`extra_args=["-p"]` at `:180`). But the Copilot CLI's `-p/--prompt <text>` takes the prompt
as an **argument** — bare `-p` exits 1 with
`error: option '-p, --prompt <text>' argument missing` and ignores stdin. So real
borrow-host extraction fails on **every OS**.

**Additional Windows-fatal bug:** `_complete_via_subprocess` guards with
`shutil.which(command)` at `:145` (which resolves `copilot` → `copilot.CMD`, so the guard
**passes**), then calls `asyncio.create_subprocess_exec(command, ...)` at `:148` with the
**bare** name `"copilot"`. On Windows `create_subprocess_exec` does no `PATHEXT` resolution,
so it raises `FileNotFoundError [WinError 2]` **before the CLI runs at all**, which `:156–157`
wraps as `HostProcessError("failed to launch host process: ...")`.

**Probe evidence (this environment — the real Copilot CLI *is* reachable and capable):**

| Variant | Invocation | Result |
|---|---|---|
| A (current code) | `create_subprocess_exec("copilot","-p")` + prompt on **stdin** | **WinError 2** (never launches) |
| E | resolved `copilot.CMD -p` + prompt on **stdin** | **rc=1: `option '-p, --prompt <text>' argument missing`** |
| C | `create_subprocess_exec(<copilot.CMD>, "-p", <prompt>, "-s")` | **rc=0 → clean JSON** (~34 s) |
| F | `copilot -p "<prompt>"` (prompt as **argument**) | **rc=0 → clean JSON** (~35 s) |

So the host agent works fine when invoked correctly (C/F); memrelay's wiring to it (A/E) is
what is broken.

**Verbatim reproduction — memrelay's _own installed code_ @ `70dfbe7`** (not a paraphrase;
a probe imports `memrelay.engine.llm.borrow_host` and calls it directly):

```text
# Probe 1 — memrelay's real CopilotHostProcess().complete() (bare "copilot"):
HostProcessError: failed to launch host process: [WinError 2] The system cannot find the file specified

# Probe 2 — same call with the WinError-2 defect bypassed (resolved copilot.CMD),
#           keeping memrelay's real ["-p"] + prompt-on-stdin:
HostProcessError: host process exited 1: error: option '-p, --prompt <text>' argument missing
```

Probe 1 is what a Windows user hits today (the launch never happens). Probe 2 proves that
_even after_ fixing the Windows launch, the same call still fails on **every** OS because
`-p` needs the prompt as an argument. Both surface as the identical `HostProcessError` the
daemon logs at extraction time.

**Blast radius — the entire zero-key default is broken on every OS.** The zero-config default
is `strategy = borrow-host` (`strategy.py:140`, `cfg.llm.strategy or "borrow-host"`) with the
default host `copilot` (`borrow_host.py:263`, `host or "copilot"`).
`BorrowHostStrategy.is_available` (`strategy.py:55–59`) probes with `shutil.which`, which
**passes** on Windows — so borrow-host is *selected and committed to* and the failure only
appears later, at extraction time (feeding the silent failure in **Wall E**). Concretely:

- **Windows:** every borrow-host extraction (Copilot **and** Claude — both use the bare command
  name) dies at launch with WinError 2.
- **Linux/macOS:** the default `copilot` host fails with the `-p` arg-missing error above.
- The **only** borrow-host config that could work is an explicit `host = claude` on Linux/macOS
  (`claude -p` _does_ read stdin) — **not** the default, and **unverified here** (no `claude` CLI
  in this environment). `byo-key` (real API key) and opt-in `local` are unaffected.

So for a first-time user on the zero-key happy path, entity extraction produces **nothing** on
every OS; recall of seeded content stays empty and `episodes_ingested` stays `0` — silently.

**Live evidence (captured daemon log, real seed of 3 commits):**

```
{"event": "Selected LLM strategy: borrow-host", "logger": "memrelay.engine.llm.strategy", ...}
{"event": "ingester: engine.note failed (attempt 1), backing off 0.350s seq=2:
           failed to launch host process: [WinError 2] The system cannot find the file specified",
 "level": "warning", "logger": "memrelay.ingest.ingester", ...}
{"event": "ingester: dropping record after 5 retries seq=2 key=bcfe5eb...",
 "level": "error", "logger": "memrelay.ingest.ingester", "exception": "Traceback ...
   memrelay/ingest/ingester.py ... engine.note ... graphiti add_episode ... extract_nodes
   ... BorrowHostLLMClient._generate_response ... CopilotHostProcess.complete
   ... _complete_via_subprocess ... create_subprocess_exec ... FileNotFoundError [WinError 2]"}
```

This proves the **real** entity-extraction LLM call is reached and fails at the diagnosed
spot; the ingester degrades gracefully (retries then poison-drops) and the **daemon does not
crash**.

**What is needed to cross this wall:** launch the **resolved** host path and pass the prompt
as the `-p` **argument** (variants C/F). Note `claude -p` *does* accept stdin, so the fix is
**per-host** and needs its own tests — it is intentionally **not** bundled into this smoke
PR. Track/fix it in a dedicated lane.

### Wall B — `litellm==1.92` is sdist-only and needs Rust (install-time)

`traceforge-toolkit` depends on `litellm>=1.0` with no upper bound, so a fresh
`pip install .` resolves the newest `litellm` (`1.92.0`), which ships **sdist-only** and
compiles native code with **Rust/Cargo**. On a machine without Cargo the install fails while
building `litellm`.

**Workaround (used in Step 0, environment-only — not a product change):** constrain to a
wheel build.

```powershell
'litellm<1.92' | Out-File -Encoding ascii constraints.txt
$env:PIP_CONSTRAINT = "constraints.txt"   # PIP_CONSTRAINT on bash
pip install .                              # resolves litellm 1.91.3 (wheel)
```

The durable fix belongs upstream (a `litellm` upper bound in `traceforge-toolkit`, or a
`litellm` cap in memrelay's own dependencies).

### Wall C — `memrelay start` first-run UX: rc≠0 within 10 s

`start_daemon` waits only `READY_TIMEOUT = 10.0` s
(`src/memrelay/daemon/lifecycle.py`) for the detached daemon to answer a health probe, then
raises `DaemonStartError("daemon did not become healthy within the timeout")` → the CLI
exits non-zero. On a real first run the detached `_serve` is still building the engine
(fastembed load, Ladybug + FTS), which routinely exceeds 10 s, so **`start` reports failure
on a start that is actually succeeding**. Poll `memrelay status` (the authoritative liveness
signal) instead of trusting `start`'s exit code. `scripts/smoke_e2e.py` and
`scripts/first_run_smoke.py` both do this (polling up to 180–240 s).

### Wall D — detached daemon can die silently during startup (intermittent)

Observed intermittently on a loaded shared machine: after `memrelay start`, the detached
daemon **never became healthy** and `daemon.pid` pointed at a **dead** process (no graph
directory was ever created) — i.e. the daemon **died during startup**, not merely a slow
one. On other runs the same detached start eventually became healthy, and a **foreground**
`memrelay _serve` was reliably healthy. Because `spawn_detached` sends the child's
stdout/stderr to `DEVNULL` (`src/memrelay/daemon/lifecycle.py`), **there is no log** to
explain the death — which is itself a first-run debuggability gap. The detachment flags
themselves are correct (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`), so this is most
likely a startup race / resource contention; it needs a dedicated investigation with the
child's stderr captured. If you hit it, retry `start`, or run `memrelay _serve` in the
foreground to see the daemon's logs.

### Wall E — the failure is silent via `status`

`memrelay status` surfaces only `sessions_observed`, `episodes_ingested`, and
`spool_pending` (`src/memrelay/cli.py`). It does **not** surface `notes_failed` or
`poison_skipped`. So after seeding, a first-time user sees the daemon **running**,
`spool_pending: 0`, `episodes_ingested: 0`, **and no error** — the extraction failure (Wall
A) is invisible unless you call `memory_note` (which *does* return the error) or read the
daemon log. Consider surfacing failure counters in `status`.

---

## How far the real-LLM path gets today (summary)

| Stage | Result at `70dfbe7` |
|---|---|
| Fresh-venv install from source | ✅ (with the `litellm<1.92` constraint — Wall B) |
| `memrelay init` (+ model download, idempotent) | ✅ |
| `memrelay start` (daemon up) | ✅ (but rc≠0 UX — Wall C; intermittently dies — Wall D) |
| `memrelay status` (healthy, counters) | ✅ |
| MCP tool surface = the three memory tools | ✅ |
| `memory_recall` (empty) round-trip, no keys/LLM | ✅ |
| `memrelay seed` (git log → spool → drain) | ✅ spool drains… |
| **Real-LLM entity extraction** | ❌ **WALLED** — borrow-host mis-invokes the host CLI (Wall A) |
| Seeded/`memory_note` facts round-trip via `memory_recall` | ❌ (blocked by Wall A) |
| `memrelay stop` / teardown | ✅ |

The real host agent is **reachable and capable** here (probes C/F succeed); memrelay's
wiring to it is what fails, before a real inference is ever billed.

---

# memrelay self-observation loop smoke (borrow-host Copilot)

A second, **narrowly scoped** live driver that proves the fix for the borrow-host
**self-observation feedback loop**: on a Copilot box the daemon observes
`~/.copilot/session-state/`, but each `copilot -p` extraction call *creates a new session
there*, which the daemon would otherwise re-observe ~2 s later and re-extract — an exponential
fan-out that burns Copilot (Opus-tier) quota and pollutes the graph with memrelay's own
extraction prompts.

- **Automated driver:** [`scripts/smoke_selfobserve_e2e.py`](../scripts/smoke_selfobserve_e2e.py)
- **Additive:** lives under `scripts/` (pytest `testpaths = ["tests"]`), so it is **never**
  collected as a test. The committed regression proof is the hermetic unit suite
  (`tests/unit/test_internal_sessions.py`, the exclusion + guard tests in
  `tests/unit/test_session_discovery.py`, and the `--session-id` assertions in
  `tests/unit/test_borrow_host_invocation.py`). This script is *validation evidence you run by
  hand* with a real, authenticated `copilot` CLI.

## What it proves (with the real `copilot` CLI, no mocks)

1. **Guard (Part B):** `warn_on_self_observation("copilot", cfg)` fires for the zero-key
   `borrow-host` + `host=copilot` default.
2. **Exclusion (Part A):** against a **real** `CopilotProvider` rooted at a scratch home, a
   fresh session id that has been `register`ed (exactly what the borrow-host path does before
   spawning `copilot`) is dropped by `active_sessions`, while a genuine observed session stays
   active.
3. **Happy path + live registration:** one synthetic fixture session is observed → spooled →
   the daemon's `Ingester` is drained **once** (performing the REAL Copilot extraction) → the
   fact is recalled from the graph; and the real extraction is shown to have **registered its
   own Copilot session id** (`internal_sessions.snapshot()` grows), i.e. the loop is closed
   live.

## ⚠️ HARD SAFETY RULE (why this is safe on a real Copilot box)

The driver **never starts a live daemon/poller** against the real `~/.copilot` (tens of
thousands of sessions — observing it *is* the loop). It:

- sets `MEMRELAY_COPILOT_HOME` to a throwaway scratch dir (hard-asserted **≠** real
  `~/.copilot`) holding exactly **one** synthetic fixture session, and a scratch `MEMRELAY_HOME`
  for the spool/graph;
- drives the pipeline **deterministically and once** in-process (`run_observe` → spool →
  `Ingester` drained once → `search`).

The real `copilot -p` extraction still writes *its* session-state under the real `~/.copilot`
(the CLI ignores `MEMRELAY_COPILOT_HOME` — that var only steers what memrelay **observes**), but
nothing observes the real home here, so there is no cadence, no re-observation, and no loop.
Cost is bounded to one fixture session = a handful of real premium/Opus-tier Copilot calls. It
also strips every `*_API_KEY` from the process env so a green can only come from the host
agent, never an inherited key.

## Run it

```powershell
# from the repo root, against THIS worktree's source
$env:PYTHONPATH = (Resolve-Path .\src).Path
python scripts\smoke_selfobserve_e2e.py
```

**Exit codes:** `0` = success (fixture observed + extracted + recalled, **and** memrelay
registered its own extraction session id); non-zero on any failure or missing prerequisite
(no `copilot` on PATH, or the scratch home resolving to the real `~/.copilot`). There is no
forced green. On success it prints an **EVIDENCE** block with the namespace,
`episodes_ingested`, recalled node/edge counts, the number of newly-registered internal ids,
and the recalled node names + edge facts.

> **Runtime:** a single real `add_episode` drives several sequential `copilot` calls, so expect
> **~10–20 min** end to end (bounded to the first episode within a 900 s drain budget). This is
> a manual validation run, not CI.

