#!/usr/bin/env python3
"""First-run smoke test for memrelay's zero-config, key-less default stack (E10-S4 / #14).

Proves the OOTB promise end-to-end **with no API keys and no host agent**:

    pip install .  ->  memrelay init  ->  memrelay start  ->  the agent has memory tools

It drives the *installed* ``memrelay`` console script over real subprocesses (it never
imports ``memrelay`` from source) in a throwaway ``MEMRELAY_HOME``, with the environment
scrubbed of every ``*_API_KEY`` (hard-asserted absent) and no host CLI installed. Steps:

1. ``memrelay init``   -> creates the home + config, registers the MCP server, and
   **downloads the embedding model** (the #13 acceptance gap). Asserts the model cache is
   now populated and the download was announced.
2. ``memrelay init``   -> idempotent re-run: fast, model **not** re-downloaded
   ("already present").
3. ``memrelay start``  -> the detached daemon builds the REAL engine (Ladybug + local
   embedder + a lazily-constructed borrow-host LLM that is never called), then serves.
4. ``memrelay status`` -> reports **running** with live health counters, proving the
   engine came up with no keys (its health probe succeeded).
5. **Agent has memory tools** -> launch the MCP stdio server exactly as a host agent would
   (``StdioServerParameters(command="memrelay", args=["mcp"])``), ``initialize`` +
   ``list_tools`` == exactly ``{memory_recall, memory_detail, memory_note}``, then
   ``call_tool("memory_recall", ...)`` round-trips agent -> MCP -> daemon -> real engine ->
   Ladybug + embedder and returns the empty-state string **without error**. This is the
   strongest key-less proof that does not need the LLM. We deliberately do NOT call
   ``memory_note`` (which needs live extraction via the absent host agent).
6. ``memrelay stop``   -> graceful shutdown; ``status`` then reports not running.

The hermetic FULL ``note -> recall`` path (proving *extraction* itself needs no key, via a
mock LLM + offline embedder) is already covered by the integration suite
(``tests/integration/test_engine_roundtrip.py`` and ``test_daemon_engine.py``); this driver
does not duplicate it.

Exit code 0 on success, non-zero on the first failed assertion (with full context printed).
Requires only the standard library plus the ``mcp`` client, both present in any
``pip install .`` of memrelay.
"""

from __future__ import annotations

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

#: The frozen MCP tool surface the agent must receive (SPEC §4.1).
EXPECTED_TOOLS = {"memory_recall", "memory_detail", "memory_note"}
#: Empty-graph recall text (memrelay.mcp.format._NO_RESULTS) — no LLM needed to produce it.
EMPTY_RECALL = "no relevant memories found"

INIT_TIMEOUT = 900.0  # first run downloads the embedding model (network-bound)
START_TIMEOUT = 60.0  # the CLI itself waits up to its own readiness window internally
STATUS_TIMEOUT = 30.0
STOP_TIMEOUT = 60.0
#: How long to wait for the detached daemon to report healthy after `start` (first run also
#: fetches Ladybug's FTS extension here, which can outlast the CLI's own readiness window).
READY_POLL_SECONDS = 180.0
TOOL_CALL_TIMEOUT = 90.0


class SmokeError(AssertionError):
    """A first-run assertion failed."""


def _log(message: str) -> None:
    print(f"[smoke] {message}", flush=True)


def _require(condition: object, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def _emit(text: str, stream) -> None:
    """Echo captured subprocess output verbatim (never truncated)."""
    if text:
        stream.write(text if text.endswith("\n") else text + "\n")
        stream.flush()


def _resolve_memrelay() -> list[str]:
    """Locate the installed ``memrelay`` console script (fall back to ``-m memrelay``)."""
    exe = shutil.which("memrelay")
    if exe:
        _log(f"using console script: {exe}")
        return [exe]
    _log("console script not on PATH; falling back to `python -m memrelay`")
    return [sys.executable, "-m", "memrelay"]


def _clean_env(home: Path, copilot: Path, ext_dir: Path) -> dict[str, str]:
    """Copy the environment, scrub all API keys, and pin memrelay to a throwaway home.

    Hard-asserts that no ``*_API_KEY`` variable survives so a green run can never be
    silently attributed to an inherited key.
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

    # Pin every memrelay-controlled location under the throwaway tree so the run is
    # hermetic and leaves nothing behind (model cache, config, MCP registration, and the
    # Ladybug FTS extension cache all live inside `tmp`).
    env["MEMRELAY_HOME"] = str(home)
    env["MEMRELAY_COPILOT_HOME"] = str(copilot)
    env["MEMRELAY_EXTENSION_DIR"] = str(ext_dir)
    env["HOME"] = str(home.parent)
    env["USERPROFILE"] = str(home.parent)
    # Make Python output unbuffered/UTF-8 so captured subprocess text is complete.
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(cmd: list[str], env: dict[str, str], timeout: float) -> subprocess.CompletedProcess:
    """Run a memrelay subcommand, echoing its full output (never truncated)."""
    _log(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeError(f"command timed out after {timeout:.0f}s: {' '.join(cmd)}") from exc
    if proc.stdout:
        _emit(proc.stdout, sys.stdout)
    if proc.stderr:
        _emit(proc.stderr, sys.stderr)
    return proc


def _combined(proc: subprocess.CompletedProcess) -> str:
    return f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()


# ─── Steps ───────────────────────────────────────────────────────────────────


def _step_init_downloads_model(
    cmd: list[str], env: dict[str, str], home: Path, copilot: Path
) -> None:
    _log("step 1: `init` creates home + config, registers MCP, downloads the model")
    proc = _run([*cmd, "init"], env, INIT_TIMEOUT)
    _require(proc.returncode == 0, f"`init` exited {proc.returncode}")

    config_file = home / "config.toml"
    _require(config_file.is_file(), f"init did not write {config_file}")
    _require(
        (copilot / "mcp-config.json").is_file(),
        "init did not register the MCP server (mcp-config.json missing)",
    )

    models_dir = home / "models"
    _require(
        models_dir.is_dir() and any(models_dir.iterdir()),
        f"embedding model was not downloaded into {models_dir}",
    )
    _require("download" in _combined(proc), "init did not announce the model download")
    _log("  model cache populated and download announced")


def _step_init_idempotent(cmd: list[str], env: dict[str, str], home: Path) -> None:
    _log("step 2: `init` re-run is idempotent and does not re-download")
    before = {p.name for p in (home / "models").iterdir()}
    proc = _run([*cmd, "init"], env, START_TIMEOUT)
    _require(proc.returncode == 0, f"idempotent `init` exited {proc.returncode}")
    _require(
        "already present" in _combined(proc),
        "second `init` did not report the model as already present (idempotency lost)",
    )
    after = {p.name for p in (home / "models").iterdir()}
    _require(after == before, "second `init` changed the model cache (unexpected re-download)")
    _log("  re-run skipped the download (model already present)")


def _step_start(cmd: list[str], env: dict[str, str]) -> None:
    _log("step 3: `start` launches the detached daemon (real engine, no keys, no host)")
    proc = _run([*cmd, "start"], env, START_TIMEOUT)
    if proc.returncode != 0:
        # The detached `_serve` keeps initializing even if the CLI's own readiness window
        # elapses (first run also fetches Ladybug's FTS extension here); step 4 confirms
        # the daemon becomes healthy authoritatively via `status`.
        _log(
            "  NOTE: `start` returned non-zero (readiness window elapsed during first-run "
            "asset provisioning); confirming liveness via `status` polling"
        )


def _step_status_running(cmd: list[str], env: dict[str, str]) -> None:
    _log("step 4: `status` reports the daemon running with live health counters")
    deadline = time.monotonic() + READY_POLL_SECONDS
    last = ""
    while time.monotonic() < deadline:
        proc = _run([*cmd, "status"], env, STATUS_TIMEOUT)
        last = proc.stdout or ""
        if "memrelay daemon: running" in last:
            for counter in ("sessions_observed", "episodes_ingested", "spool_pending"):
                _require(counter in last, f"status is missing health counter {counter!r}")
            _log("  daemon healthy with all live health counters present")
            return
        time.sleep(2.0)
    raise SmokeError(
        f"daemon never became healthy within {READY_POLL_SECONDS:.0f}s; last status:\n{last}"
    )


async def _step_agent_tools(cmd: list[str], env: dict[str, str], home: Path) -> None:
    _log("step 5: agent gets exactly the three memory tools and `memory_recall` round-trips")
    params = StdioServerParameters(command=cmd[0], args=[*cmd[1:], "mcp"], env=env, cwd=str(home))
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        listed = await session.list_tools()
        names = {tool.name for tool in listed.tools}
        _require(
            names == EXPECTED_TOOLS,
            f"agent tool surface mismatch: got {sorted(names)}, want {sorted(EXPECTED_TOOLS)}",
        )
        _log(f"  agent sees exactly: {sorted(names)}")

        result = await session.call_tool(
            "memory_recall",
            {"query": "did the first-run smoke wire memory end to end?"},
            read_timeout_seconds=timedelta(seconds=TOOL_CALL_TIMEOUT),
        )
        _require(not result.isError, f"memory_recall returned an error: {result.content}")
        text = "".join(getattr(block, "text", "") for block in result.content)
        _require(text.strip() != "", "memory_recall returned empty content")
        _require(
            EMPTY_RECALL in text.lower(),
            f"memory_recall did not return the empty-state text; got: {text!r}",
        )
        _log("  memory_recall round-tripped agent -> MCP -> daemon -> engine (no keys, no LLM)")


def _step_stop(cmd: list[str], env: dict[str, str]) -> None:
    _log("step 6: `stop` shuts the daemon down and `status` confirms it is gone")
    proc = _run([*cmd, "stop"], env, STOP_TIMEOUT)
    _require(proc.returncode == 0, f"`stop` exited {proc.returncode}")
    _require("stopped" in _combined(proc), "`stop` did not confirm the daemon stopped")

    deadline = time.monotonic() + STATUS_TIMEOUT
    while time.monotonic() < deadline:
        proc = _run([*cmd, "status"], env, STATUS_TIMEOUT)
        if "memrelay daemon: not running" in (proc.stdout or ""):
            _log("  daemon confirmed not running")
            return
        time.sleep(1.0)
    raise SmokeError("daemon still reachable after `stop`")


def _best_effort_stop(cmd: list[str], env: dict[str, str]) -> None:
    try:
        subprocess.run(
            [*cmd, "stop"], env=env, timeout=STOP_TIMEOUT, capture_output=True, text=True
        )
    except Exception:  # noqa: BLE001 - cleanup must never mask the real result
        pass


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="memrelay-smoke-"))
    home = tmp / "home"
    copilot = tmp / "copilot"
    ext_dir = tmp / "extensions"
    env = _clean_env(home, copilot, ext_dir)
    cmd = _resolve_memrelay()
    _log(f"MEMRELAY_HOME={home}")

    try:
        _step_init_downloads_model(cmd, env, home, copilot)
        _step_init_idempotent(cmd, env, home)
        _step_start(cmd, env)
        _step_status_running(cmd, env)
        asyncio.run(_step_agent_tools(cmd, env, home))
        _step_stop(cmd, env)
    except SmokeError as exc:
        print(f"\n[smoke] FAILED: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        _best_effort_stop(cmd, env)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[smoke] PASSED: zero-config, key-less first run works end to end.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
