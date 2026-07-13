#!/usr/bin/env python3
"""Live end-to-end smoke of memrelay's REAL borrow-host LLM extraction path.

This is the one validation the test suite structurally cannot cover: every
integration test swaps in a deterministic ``MockLLMClient`` for entity
extraction (see ``tests/integration/test_engine_roundtrip.py`` and
``tests/daemon/...``), so the *real* borrow-host extraction path — the daemon
shelling out to a live host agent (Copilot/Claude) to extract entities — is
never exercised by CI. ``scripts/first_run_smoke.py`` proves the key-less,
no-LLM promise but *deliberately skips* ``memory_note`` (which needs live
extraction). This driver picks up exactly there.

It runs against the **installed, unmodified** ``memrelay`` console script over
real subprocesses (it never imports ``memrelay`` from source, and never mocks
anything) in a throwaway ``MEMRELAY_HOME``, with the environment scrubbed of
every ``*_API_KEY`` (hard-asserted absent) so a green can never be silently
attributed to an inherited key. There is no fallback that fabricates success.

Two phases:

* **Phase 1 — key-less, no-LLM (MUST PASS).** init (+ model download) → idempotent
  init → start → status running → the agent gets exactly the three memory tools →
  ``memory_recall`` round-trips agent → MCP → daemon → real engine and returns the
  empty-state string. Mirrors ``first_run_smoke.py``; this is the guaranteed-green
  subset. Run it alone with ``--phase1-only`` (CI-safe, no host agent required).

* **Phase 2 — real-LLM extraction (attempts the real path, reports the wall).**
  Reuses the Phase-1 daemon and drives real extraction through the MCP tool
  ``memory_note("<canary fact>")`` (synchronous: the daemon runs the borrow-host
  LLM inline), then ``memory_recall`` and checks whether the canary round-trips.
  Requires a host agent CLI (``copilot`` or ``claude``) on PATH — that is the
  borrow-host prerequisite. **No mocks, no forced green.**

Exit codes (unambiguous, honest):

* ``0`` — Phase 1 passed AND (in full mode) the real-LLM canary round-tripped.
          Also returned by ``--phase1-only`` when Phase 1 passes.
* ``1`` — Phase 1 failed (a real key-less regression, or the daemon never became
          healthy). Blocking.
* ``2`` — Phase 2 not attempted: the borrow-host prerequisite is missing (no
          ``copilot``/``claude`` on PATH). The real-LLM path was NOT exercised.
* ``3`` — Phase 2 attempted but **WALLED**: extraction failed or the canary did
          not round-trip. This is the EXPECTED result today (see docs/SMOKE.md,
          "Known walls"): the borrow-host client mis-invokes the host CLI, so
          real extraction never succeeds. When the borrow-host fix lands this
          driver flips to exit 0 and becomes the regression guard.

Requires only the standard library plus the ``mcp`` client, both present in any
``pip install .`` of memrelay.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ─── Frozen contract constants (kept in step with first_run_smoke.py) ─────────

#: The MCP tool surface the agent must receive (SPEC §4.1).
EXPECTED_TOOLS = {"memory_recall", "memory_detail", "memory_note"}
#: Empty-graph recall text (memrelay.mcp.format) — produced with no LLM.
EMPTY_RECALL = "no relevant memories found"
#: The host agent CLIs borrow-host knows how to drive (borrow_host.HOST_PROCESSES).
HOST_AGENT_CLIS = ("copilot", "claude")

#: A distinctive fact to store then recall. The rare token ``zephyrine`` makes the
#: round-trip check unambiguous: if extraction really ran, recall must surface it.
CANARY_TOKEN = "zephyrine"
CANARY_FACT = (
    "MEMRELAY_SMOKE_CANARY: The Zephyrine Protocol was ratified by the Qtown "
    "working group and is maintained by the memrelay live-smoke harness."
)
CANARY_QUERY = "What is the Zephyrine Protocol and who maintains it?"

INIT_TIMEOUT = 900.0  # first run downloads the embedding model (network-bound)
START_TIMEOUT = 60.0
STATUS_TIMEOUT = 30.0
STOP_TIMEOUT = 60.0
#: How long to wait for the detached daemon to report healthy after `start`. The
#: CLI's own readiness window is only READY_TIMEOUT=10s (daemon/lifecycle.py), far
#: shorter than a real first-run engine build (fastembed load + Ladybug FTS fetch),
#: so `start` routinely returns rc=1 while the daemon keeps coming up. We poll
#: `status` — the authoritative liveness signal — for much longer.
READY_POLL_SECONDS = 240.0
TOOL_CALL_TIMEOUT = 120.0
#: After a successful note, allow the synchronous extraction result to settle
#: before recall (memory_note is synchronous, so this is a small safety margin).
RECALL_SETTLE_SECONDS = 2.0


class SmokeError(AssertionError):
    """A Phase-1 (must-pass) assertion failed."""


def _unwrap(exc: BaseException) -> str:
    """Flatten an anyio/mcp ExceptionGroup down to a concise leaf message."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


def _drive(coro) -> str | None:
    """Run an async MCP step, returning its reason string (or a normalized error message).

    The mcp/anyio client re-wraps any exception raised inside its task groups in an
    ExceptionGroup; catching and unwrapping here keeps an unexpected transport failure from
    dumping a raw traceback and lets the caller treat it as an ordinary non-green reason. The
    step functions themselves *return* their failure reason (they do not raise) precisely so
    a detected wall never has to traverse — and get mangled by — that task-group machinery.
    """
    try:
        return asyncio.run(coro)
    except Exception as exc:  # noqa: BLE001 - normalized into a reason string for the banner
        return _unwrap(exc)


def _log(message: str) -> None:
    print(f"[smoke] {message}", flush=True)


def _banner(lines: list[str]) -> None:
    width = max(len(line) for line in lines) + 4
    bar = "=" * width
    print("\n" + bar, flush=True)
    for line in lines:
        print(f"= {line.ljust(width - 4)} =", flush=True)
    print(bar + "\n", flush=True)


def _require(condition: object, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def _emit(text: str, stream) -> None:
    if text:
        stream.write(text if text.endswith("\n") else text + "\n")
        stream.flush()


def _resolve_memrelay() -> list[str]:
    exe = shutil.which("memrelay")
    if exe:
        _log(f"using console script: {exe}")
        return [exe]
    _log("console script not on PATH; falling back to `python -m memrelay`")
    return [sys.executable, "-m", "memrelay"]


def _clean_env(home: Path, copilot: Path, ext_dir: Path) -> dict[str, str]:
    """Copy the environment, scrub all API keys, and pin memrelay to a throwaway home.

    Unlike ``first_run_smoke.py`` we deliberately KEEP the real ``HOME``/``USERPROFILE``
    and ``PATH`` intact: Phase 2 needs the host agent CLI to be reachable and able to
    authenticate with the user's existing subscription (that is the whole point of
    "borrow-host"). Only the *API keys* are scrubbed (and asserted absent), so a green
    can still never come from an inherited key — it can only come from the host agent.
    Every memrelay-controlled location is pinned under the throwaway tree.
    """
    env = dict(os.environ)
    key_pattern = re.compile(r"(_API_KEY|_API_TOKEN|_ACCESS_KEY|_SECRET_KEY)$", re.IGNORECASE)
    explicit = {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "COHERE_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "VOYAGE_API_KEY",
    }
    for name in list(env):
        if name in explicit or key_pattern.search(name):
            env.pop(name, None)

    leaked = sorted(n for n in env if n.upper().endswith("_API_KEY"))
    _require(not leaked, f"environment still exposes API keys after scrub: {leaked}")

    env["MEMRELAY_HOME"] = str(home)
    env["MEMRELAY_COPILOT_HOME"] = str(copilot)
    env["MEMRELAY_EXTENSION_DIR"] = str(ext_dir)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _run(cmd: list[str], env: dict[str, str], timeout: float) -> subprocess.CompletedProcess:
    _log(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, env=env, timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired as exc:
        raise SmokeError(f"command timed out after {timeout:.0f}s: {' '.join(cmd)}") from exc
    if proc.stdout:
        _emit(proc.stdout, sys.stdout)
    if proc.stderr:
        _emit(proc.stderr, sys.stderr)
    return proc


def _combined(proc: subprocess.CompletedProcess) -> str:
    return f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()


# ─── Phase 1 — key-less, no-LLM (MUST PASS) ──────────────────────────────────


def _step_init_downloads_model(
    cmd: list[str], env: dict[str, str], home: Path, copilot: Path
) -> None:
    _log("step 1: `init` creates home + config, registers MCP, downloads the model")
    proc = _run([*cmd, "init"], env, INIT_TIMEOUT)
    _require(proc.returncode == 0, f"`init` exited {proc.returncode}")

    _require((home / "config.toml").is_file(), f"init did not write {home / 'config.toml'}")
    _require(
        (copilot / "mcp-config.json").is_file(),
        "init did not register the MCP server (mcp-config.json missing)",
    )
    models_dir = home / "models"
    _require(
        models_dir.is_dir() and any(models_dir.iterdir()),
        f"embedding model was not downloaded into {models_dir}",
    )
    _log("  model cache populated")


def _step_init_idempotent(cmd: list[str], env: dict[str, str], home: Path) -> None:
    _log("step 2: `init` re-run is idempotent and does not re-download")
    before = {p.name for p in (home / "models").iterdir()}
    proc = _run([*cmd, "init"], env, START_TIMEOUT)
    _require(proc.returncode == 0, f"idempotent `init` exited {proc.returncode}")
    after = {p.name for p in (home / "models").iterdir()}
    _require(after == before, "second `init` changed the model cache (unexpected re-download)")
    _log("  re-run left the model cache unchanged")


def _step_start(cmd: list[str], env: dict[str, str]) -> None:
    _log("step 3: `start` launches the detached daemon (real engine, no keys)")
    proc = _run([*cmd, "start"], env, START_TIMEOUT)
    if proc.returncode != 0:
        # Expected on a real first run: the CLI's 10s readiness window (READY_TIMEOUT)
        # elapses while the detached `_serve` is still building the engine. Step 4
        # confirms liveness authoritatively via `status`.
        _log(
            "  NOTE: `start` returned non-zero (its 10s readiness window elapsed during "
            "first-run engine build); confirming liveness via `status` polling"
        )


def _step_status_running(cmd: list[str], env: dict[str, str]) -> None:
    _log("step 4: `status` reports the daemon running with live health counters")
    deadline = time.monotonic() + READY_POLL_SECONDS
    last = ""
    while time.monotonic() < deadline:
        proc = _run([*cmd, "status"], env, STATUS_TIMEOUT)
        last = proc.stdout or ""
        # Exact line — "running" alone also matches "not running".
        if "memrelay daemon: running" in last:
            for counter in ("sessions_observed", "episodes_ingested", "spool_pending"):
                _require(counter in last, f"status is missing health counter {counter!r}")
            _log("  daemon healthy with all live health counters present")
            return
        time.sleep(2.0)
    raise SmokeError(
        f"daemon never became healthy within {READY_POLL_SECONDS:.0f}s; last status:\n{last}\n"
        "(observed intermittently on a loaded machine: the detached daemon can die during "
        "startup — its stdout/stderr go to DEVNULL so there is no log; see docs/SMOKE.md)."
    )


async def _phase1_agent_tools(cmd: list[str], env: dict[str, str], home: Path) -> str | None:
    """Return an error reason if the key-less tool round-trip fails, else None.

    Reasons are *returned* (not raised) so a failure inside the mcp/anyio task-group context
    is not re-wrapped in an ExceptionGroup that would slip past a plain ``except``.
    """
    _log("step 5: agent gets exactly the three memory tools and `memory_recall` round-trips")
    params = StdioServerParameters(command=cmd[0], args=[*cmd[1:], "mcp"], env=env, cwd=str(home))
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        listed = await session.list_tools()
        names = {tool.name for tool in listed.tools}
        if names != EXPECTED_TOOLS:
            return (
                f"agent tool surface mismatch: got {sorted(names)}, want {sorted(EXPECTED_TOOLS)}"
            )
        _log(f"  agent sees exactly: {sorted(names)}")

        result = await session.call_tool(
            "memory_recall",
            {"query": "did the live smoke wire memory end to end?"},
            read_timeout_seconds=timedelta(seconds=TOOL_CALL_TIMEOUT),
        )
        if result.isError:
            return f"memory_recall returned an error: {result.content}"
        text = "".join(getattr(block, "text", "") for block in result.content)
        if text.strip() == "":
            return "memory_recall returned empty content"
        if EMPTY_RECALL not in text.lower():
            return f"memory_recall did not return the empty-state text; got: {text!r}"
        _log("  memory_recall round-tripped agent -> MCP -> daemon -> engine (no keys, no LLM)")
    return None


# ─── Phase 2 — real-LLM extraction (attempts the real path, reports the wall) ─


def _detect_host_agent() -> str | None:
    for cli in HOST_AGENT_CLIS:
        if shutil.which(cli):
            return cli
    return None


async def _phase2_real_extraction(cmd: list[str], env: dict[str, str], home: Path) -> str | None:
    """Store a canary via real extraction, then recall it.

    Returns a wall-reason string if the real-LLM note -> extract -> recall round-trip does
    not complete, else None. Reasons are *returned* (not raised) so a wall detected inside
    the mcp/anyio task-group context does not escape wrapped in an ExceptionGroup.
    """
    _log("step 6: REAL-LLM path — `memory_note` drives borrow-host extraction inline")
    params = StdioServerParameters(command=cmd[0], args=[*cmd[1:], "mcp"], env=env, cwd=str(home))
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        note = await session.call_tool(
            "memory_note",
            {"content": CANARY_FACT},
            read_timeout_seconds=timedelta(seconds=TOOL_CALL_TIMEOUT),
        )
        note_text = "".join(getattr(block, "text", "") for block in note.content)
        if note.isError:
            return (
                "memory_note returned isError=True — the daemon's borrow-host LLM call "
                f"failed during entity extraction. Backend error:\n    {note_text.strip()}"
            )
        if "noted" not in note_text.lower():
            return f"memory_note did not confirm storage (expected 'Noted.'); got: {note_text!r}"
        _log(
            "  memory_note stored the canary (host agent extraction reported success): "
            f"{note_text.strip()!r}"
        )

        time.sleep(RECALL_SETTLE_SECONDS)

        _log("step 7: `memory_recall` must surface the canary fact (real round-trip)")
        recall = await session.call_tool(
            "memory_recall",
            {"query": CANARY_QUERY},
            read_timeout_seconds=timedelta(seconds=TOOL_CALL_TIMEOUT),
        )
        recall_text = "".join(getattr(block, "text", "") for block in recall.content)
        if recall.isError:
            return f"memory_recall returned an error: {recall.content}"
        if EMPTY_RECALL in recall_text.lower() or CANARY_TOKEN not in recall_text.lower():
            return (
                "the canary fact did NOT round-trip: memory_note reported success but "
                f"memory_recall for {CANARY_QUERY!r} did not surface {CANARY_TOKEN!r}.\n"
                f"    recall returned: {recall_text.strip()!r}"
            )
        _log(f"  canary round-tripped: recall surfaced {CANARY_TOKEN!r} from the real graph")
    return None


def _best_effort_stop(cmd: list[str], env: dict[str, str]) -> None:
    try:
        subprocess.run(
            [*cmd, "stop"], env=env, timeout=STOP_TIMEOUT, capture_output=True, text=True
        )
    except Exception:  # noqa: BLE001 - cleanup must never mask the real result
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase1-only",
        action="store_true",
        help="Run only the key-less, no-LLM subset (CI-safe; no host agent required).",
    )
    parser.add_argument(
        "--keep-home",
        action="store_true",
        help="Do not delete the throwaway MEMRELAY_HOME on exit (for inspection).",
    )
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="memrelay-e2e-"))
    home = tmp / "home"
    copilot = tmp / "copilot"
    ext_dir = tmp / "extensions"
    env = _clean_env(home, copilot, ext_dir)
    cmd = _resolve_memrelay()
    _log(f"MEMRELAY_HOME={home}")

    try:
        # Phase 1 — must pass.
        try:
            _step_init_downloads_model(cmd, env, home, copilot)
            _step_init_idempotent(cmd, env, home)
            _step_start(cmd, env)
            _step_status_running(cmd, env)
            err = _drive(_phase1_agent_tools(cmd, env, home))
            if err:
                raise SmokeError(err)
        except SmokeError as exc:
            _banner(["PHASE 1 FAILED (key-less no-LLM path)", str(exc)])
            return 1

        if args.phase1_only:
            _banner(
                [
                    "PHASE 1 PASSED",
                    "Key-less, no-LLM first run works end to end.",
                    "(--phase1-only: real-LLM path not attempted.)",
                ]
            )
            return 0

        # Phase 2 — real-LLM extraction.
        host = _detect_host_agent()
        if host is None:
            _banner(
                [
                    "PHASE 2 NOT ATTEMPTED — prerequisite missing",
                    f"No host agent CLI on PATH (looked for: {', '.join(HOST_AGENT_CLIS)}).",
                    "borrow-host needs a live host agent to extract entities.",
                    "Install & authenticate Copilot or Claude CLI, then re-run.",
                ]
            )
            return 2
        _log(f"host agent detected on PATH: {host} ({shutil.which(host)})")

        reason = _drive(_phase2_real_extraction(cmd, env, home))
        if reason:
            _banner(
                [
                    f"WALLED AT REAL-LLM EXTRACTION (host agent: {host})",
                    "The daemon reached the borrow-host LLM call but could not complete a",
                    "note -> extract -> recall round-trip. Details below and in docs/SMOKE.md.",
                    "",
                    *reason.splitlines(),
                ]
            )
            return 3
    finally:
        _best_effort_stop(cmd, env)
        if not args.keep_home:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            _log(f"kept MEMRELAY_HOME for inspection: {home}")

    _banner(
        [
            "PHASE 2 PASSED — REAL-LLM PATH GREEN",
            "A real host-agent extraction stored a fact and it round-tripped through recall.",
            "The full first-time-user happy path works end to end with a live borrow-host LLM.",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
