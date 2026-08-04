"""Borrow-host LLM strategy (E4-S2 / #35).

``BorrowHostLLMClient`` implements graphiti-core's ``LLMClient`` without any API
key by *borrowing the host agent's own model*: it renders graphiti's structured
prompt to plain text, appends the requested ``response_model`` JSON schema, and
asks a host process to complete it, then robustly parses JSON back out.

The actual host inference call is isolated behind the small :class:`HostProcess`
protocol (``async complete(prompt) -> str``) so it can be faked in tests. The
real subprocess implementations (:class:`CopilotHostProcess`,
:class:`ClaudeHostProcess`) are best-effort and MUST NOT be required for the
hermetic gate; ``host=<agent-id>`` selects one via the :data:`HOST_PROCESSES`
registry (see :func:`resolve_host_process`), and an unregistered host yields a
fail-loud :class:`_UnknownHostProcess` rather than a silent Copilot fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import uuid
from typing import Any, Protocol, runtime_checkable

from graphiti_core.llm_client.client import LLMClient, ModelSize
from graphiti_core.llm_client.config import LLMConfig as GraphitiLLMConfig
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from memrelay import internal_sessions

logger = logging.getLogger(__name__)

# ``_node_shim_launch`` runs on every Copilot extraction call, so a persistent Windows
# misconfiguration (shim present but its node loader / ``node`` missing) must NOT warn once per
# call — that would flood the daemon log. Each distinct reason warns once per process, then drops
# to debug. Reset only matters for tests; production keeps it for the process lifetime.
_warned_shim_fallbacks: set[str] = set()


def _warn_shim_fallback_once(reason: str, message: str, *args: object) -> None:
    """Warn about a node-shim bypass fallback at most once per process per ``reason``."""
    if reason in _warned_shim_fallbacks:
        logger.debug(message, *args)
        return
    _warned_shim_fallbacks.add(reason)
    logger.warning(message, *args)


def _create_windows_job(pid: int) -> Any | None:
    """Assign ``pid`` to an owned kill-on-close Job Object when available.

    Contract: never raises. The caller has already spawned the host process, so an
    exception escaping here would leave that process neither terminated nor reaped.
    Job Objects are an optimization over the exact-PID ``taskkill /T`` fallback, so
    any failure — a pywin32 API/constant change, an unexpected
    ``QueryInformationJobObject`` shape, a job the parent may not nest under —
    degrades to ``None`` rather than breaking the call.
    """
    if os.name != "nt":
        return None
    try:
        import win32api
        import win32job
    except ImportError:
        logger.debug("pywin32 unavailable for host PID %s", pid)
        return None

    job = None
    try:
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(
            job,
            win32job.JobObjectExtendedLimitInformation,
            info,
        )
        process_handle = win32api.OpenProcess(0x0001 | 0x0100, False, pid)
        try:
            win32job.AssignProcessToJobObject(job, process_handle)
        finally:
            process_handle.Close()
        return job
    except Exception:  # noqa: BLE001 - fall back to exact-PID taskkill, never break the call
        logger.debug("Windows Job Object unavailable for host PID %s", pid, exc_info=True)
        if job is not None:
            try:
                job.Close()
            except Exception:  # noqa: BLE001 - nothing left to salvage
                pass
        return None


def _close_windows_job(job: Any | None) -> None:
    if job is not None:
        try:
            job.Close()
        except Exception:  # noqa: BLE001 - closing is best-effort cleanup
            logger.debug("failed to close host Job Object", exc_info=True)


DEFAULT_MAX_TOKENS = 16384


class HostProcessError(RuntimeError):
    """Raised when the host inference process cannot produce a completion."""


@runtime_checkable
class HostProcess(Protocol):
    """Seam over a single host LLM completion call.

    Implementations take a fully-rendered prompt and return the model's raw text
    response. This is the only part of borrow-host that touches the outside
    world, which is exactly why it is a tiny, fakeable protocol.
    """

    async def complete(self, prompt: str) -> str: ...


def _render_messages(messages: list[Message]) -> str:
    """Flatten graphiti's role/content messages into a single prompt string."""
    return "\n\n".join(f"{message.role}: {message.content}" for message in messages)


def _schema_instruction(response_model: type[BaseModel]) -> str:
    schema = json.dumps(response_model.model_json_schema(), indent=2)
    return (
        "Respond with a SINGLE JSON object and nothing else — no prose, no code "
        "fences, no explanation. The object MUST validate against this JSON "
        f"schema:\n{schema}"
    )


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop the opening fence line (``` or ```json) and the trailing fence.
    without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if "```" in without_open:
        without_open = without_open.rsplit("```", 1)[0]
    return without_open.strip()


def _loads_json_object(raw: str) -> dict[str, Any]:
    """Best-effort parse of a JSON object out of a raw model response."""
    candidate = _strip_code_fences(raw)
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError):
        # Fall back to the outermost {...} span if the model added stray text.
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found in host response") from None
        parsed = json.loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("host response JSON was not an object")
    return parsed


class BorrowHostLLMClient(LLMClient):
    """graphiti ``LLMClient`` backed by a host-process completion + JSON parse."""

    def __init__(
        self,
        host_process: HostProcess,
        config: GraphitiLLMConfig | None = None,
        *,
        max_json_retries: int = 2,
    ) -> None:
        super().__init__(config, cache=False)
        self._host = host_process
        self._max_json_retries = max_json_retries

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        base_prompt = _render_messages(messages)
        if response_model is not None:
            base_prompt = f"{base_prompt}\n\n{_schema_instruction(response_model)}"

        prompt = base_prompt
        last_error: Exception | None = None
        for attempt in range(self._max_json_retries + 1):
            raw = await self._host.complete(prompt)
            try:
                return _loads_json_object(raw)
            except ValueError as exc:
                last_error = exc
                logger.debug("borrow-host JSON parse failed (attempt %d): %s", attempt + 1, exc)
                prompt = (
                    f"{base_prompt}\n\nYour previous reply was not valid JSON "
                    f"({exc}). Reply again with ONLY the JSON object."
                )
        raise HostProcessError(
            f"borrow-host could not obtain valid JSON after "
            f"{self._max_json_retries + 1} attempts: {last_error}"
        )


def _extract_loader_from_shim(shim_path: str) -> str | None:
    """Extract the ``.js`` loader path a Windows npm ``.cmd``/``.bat`` shim launches.

    The shim ends in a line that runs ``node "<dir>/npm-loader.js" %*``. We take the first
    quoted ``*.js`` token and resolve its ``%dp0%``/``%~dp0`` variable (the shim's directory).
    Returns a normalized absolute path, or ``None`` if the shim can't be read or has no ``.js``
    reference — the caller then tries the conventional layout, else execs the shim directly.
    """
    try:
        with open(shim_path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None
    match = re.search(r'"([^"\r\n]*\.js)"', text)
    if match is None:
        return None
    shim_dir = os.path.dirname(shim_path)
    # ``%dp0%``/``%~dp0`` expands to the shim's directory. Use a function replacement so the
    # backslashes in a Windows path aren't interpreted as regex escape sequences.
    resolved = re.sub(
        r"%~?dp0%?", lambda _m: shim_dir + os.sep, match.group(1), flags=re.IGNORECASE
    )
    return os.path.normpath(resolved)


def _node_shim_launch(resolved: str) -> tuple[str, str] | None:
    """Return ``(node, loader)`` to launch a Windows npm shim via node, else ``None``.

    Running ``copilot.CMD`` goes through **cmd.exe** (command line capped at 8191 chars);
    borrow-host's extraction prompts are larger, so cmd aborts with "command line is too long"
    before Copilot starts. The shim just runs ``node "<...>/npm-loader.js" %*``, so invoking
    ``node`` on that loader ourselves routes through ``CreateProcess`` (32767-char cap) instead.

    Returns ``None`` — caller execs ``resolved`` directly, as before — when this isn't a Windows
    ``.cmd``/``.bat`` shim or the loader/``node`` can't be found (small prompts still work; no
    hard regression on any platform).
    """
    if sys.platform != "win32":
        logger.debug("borrow-host: node-shim bypass n/a (not win32); exec resolved %r", resolved)
        return None
    if not resolved.lower().endswith((".cmd", ".bat")):
        logger.debug("borrow-host: node-shim bypass n/a (not a shim); exec resolved %r", resolved)
        return None
    shim_dir = os.path.dirname(resolved)
    loader = _extract_loader_from_shim(resolved)
    if loader is None or not os.path.isfile(loader):
        conventional = os.path.join(shim_dir, "node_modules", "@github", "copilot", "npm-loader.js")
        loader = conventional if os.path.isfile(conventional) else None
    if loader is None:
        _warn_shim_fallback_once(
            "loader-missing",
            "borrow-host: no node loader found for shim %r; running the shim directly "
            "(large prompts may overflow the cmd.exe command line)",
            resolved,
        )
        return None
    sibling = os.path.join(shim_dir, "node.exe")
    node = sibling if os.path.isfile(sibling) else shutil.which("node")
    if node is None:
        _warn_shim_fallback_once(
            "node-missing",
            "borrow-host: shim %r found but 'node' is not on PATH; running the shim directly",
            resolved,
        )
        return None
    logger.debug("borrow-host: launching shim %r via node %r loader %r", resolved, node, loader)
    return node, loader


async def _run_host_cli(
    command: str,
    argv: list[str],
    *,
    stdin_payload: bytes | None = None,
    bypass_windows_shim: bool = False,
) -> str:
    """Launch the *resolved* host CLI ``command`` with ``argv``; return its stdout text.

    Shared by the best-effort host-process implementations (Copilot, Claude). Two details it
    deliberately gets right — both were the borrow-host wall (see ``docs/SMOKE.md`` Wall A):

    * **Resolved path.** ``shutil.which`` is used both as the availability guard *and* as the
      value handed to :func:`asyncio.create_subprocess_exec`. On Windows that resolves
      ``copilot`` → ``copilot.CMD``; passing the bare name would raise ``FileNotFoundError``
      (``WinError 2``) because ``create_subprocess_exec`` does no ``PATHEXT`` lookup.
    * **Per-host prompt delivery.** The prompt is *not* assumed to arrive on stdin. Callers pass
      the fully-built ``argv`` (so a host that wants the prompt as an argument — ``copilot -p
      <text>`` — puts it there) and, only for a host that reads stdin (``claude -p``), a
      ``stdin_payload``.
    * **Windows cmd-overflow bypass (opt-in).** With ``bypass_windows_shim=True`` (Copilot only),
      a resolved Windows ``.cmd``/``.bat`` npm shim is launched as ``node <npm-loader.js> *argv``
      via :func:`_node_shim_launch` — ``CreateProcess`` (32767 chars) instead of ``cmd.exe`` (8191)
      — so >8 KB extraction prompts no longer abort with "The command line is too long." Any
      non-shim, non-Windows, or unresolvable case execs ``resolved`` directly (never worse than
      before); Claude keeps ``bypass_windows_shim=False`` (its prompt rides stdin, so it can't
      overflow argv).

    Kept out of the hermetic gate for the *real* subprocess on purpose (the exact CLI is
    environment- and version-dependent), but the *invocation shape* — resolved path, argv, and
    stdin-vs-arg prompt delivery — is asserted hermetically (patching ``create_subprocess_exec``
    and ``shutil.which``) in ``tests/unit/test_borrow_host_invocation.py``.
    """
    resolved = shutil.which(command)
    if resolved is None:
        raise HostProcessError(f"host command {command!r} not found on PATH")
    launch: list[str] = [resolved]
    if bypass_windows_shim:
        node_loader = _node_shim_launch(resolved)
        if node_loader is not None:
            launch = [node_loader[0], node_loader[1]]
    spawn_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        spawn_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        spawn_kwargs["start_new_session"] = True
    process_job: Any | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *launch,
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **spawn_kwargs,
        )
        pid = getattr(process, "pid", None)
        if isinstance(pid, int):
            process_job = _create_windows_job(pid)
    except OSError as exc:  # pragma: no cover - environment dependent
        raise HostProcessError(f"failed to launch host process: {exc}") from exc
    try:
        stdout, stderr = await process.communicate(stdin_payload)
    except asyncio.CancelledError:
        await _terminate_process_tree(process, windows_job=process_job)
        raise
    except OSError as exc:  # pragma: no cover - environment dependent
        raise HostProcessError(f"failed to communicate with host process: {exc}") from exc
    finally:
        _close_windows_job(process_job)
    if process.returncode != 0:
        raise HostProcessError(
            f"host process exited {process.returncode}: {stderr.decode('utf-8', 'replace').strip()}"
        )
    return stdout.decode("utf-8", "replace")


async def _terminate_process_tree(
    process: asyncio.subprocess.Process,
    *,
    windows_job: Any | None = None,
) -> None:
    """Terminate only the process tree rooted at memrelay's spawned host PID."""
    if os.name == "nt":
        terminated_job = False
        if windows_job is not None:
            try:
                import win32job
            except ImportError:
                logger.debug("failed to terminate host Job Object", exc_info=True)
            else:
                try:
                    win32job.TerminateJobObject(windows_job, 1)
                    terminated_job = True
                except Exception:  # noqa: BLE001 - fall through to exact-PID taskkill
                    logger.debug("failed to terminate host Job Object", exc_info=True)
        if not terminated_job:
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            except OSError:
                logger.debug("taskkill unavailable while cancelling host PID %s", process.pid)
    elif process.returncode is None:
        # Only signal the group while the child is unreaped. Once asyncio has
        # reaped it, the PID (and therefore the PGID, since ``start_new_session``
        # makes them equal) is back in the OS pool and could name an unrelated
        # group — signalling it would be exactly the broad kill this path exists
        # to avoid. Nothing is leaked by skipping: the child is already gone, and
        # its descendants keep the stdout/stderr pipes open, so ``communicate()``
        # would not have returned while any of them were alive.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    if process.returncode is None:
        await process.wait()
    try:
        await process.communicate()
    except (BrokenPipeError, ConnectionResetError):
        pass


class CopilotHostProcess:
    """Best-effort Copilot CLI subprocess implementation of :class:`HostProcess`.

    Wires borrow-host to a locally installed Copilot CLI. Copilot's ``-p/--prompt`` takes the
    prompt as a **command-line argument** — bare ``-p`` exits 1 with
    ``option '-p, --prompt <text>' argument missing`` and *ignores* stdin — so, unlike
    :class:`ClaudeHostProcess`, the prompt is placed in ``argv`` rather than sent on stdin.
    ``-s/--silent`` keeps stdout to just the agent response with no run stats, so the JSON parse
    stays clean (``copilot --help``: "Output only the agent response (no stats), useful for
    scripting with -p"). It is intentionally *best effort*: the real subprocess path is NOT
    exercised by the hermetic gate (which uses a deterministic mock). Availability is discovered
    via ``shutil.which`` so the strategy layer can fall back cleanly when Copilot is not installed.

    On Windows ``shutil.which`` resolves ``copilot`` to an npm ``copilot.CMD`` shim; since the
    prompt rides in ``argv`` and extraction prompts exceed cmd.exe's 8191-char limit, this host
    opts into a node-direct launch (``bypass_windows_shim=True``) so large prompts don't overflow.

    **Self-observation break.** Each ``complete`` mints a fresh ``uuid4`` and invokes
    ``copilot --session-id <uuid> -p <prompt> -s``, registering that id via
    :mod:`memrelay.internal_sessions` *before* the CLI runs. Because every ``copilot -p`` creates
    a new ``~/.copilot/session-state/<id>/events.jsonl`` in the very tree the daemon observes,
    controlling and registering the id lets the poller's
    :func:`~memrelay.daemon.session_discovery.active_sessions` skip memrelay's own extraction
    sessions — the fix for the borrow-host feedback loop. This is Copilot-specific;
    :class:`ClaudeHostProcess` writes to ``~/.claude`` (unobserved) and is left untouched.
    """

    def __init__(self, command: str = "copilot", extra_args: list[str] | None = None) -> None:
        self._command = command
        # Flags placed AFTER the ``-p <prompt>`` pair. ``-s/--silent`` trims run stats so stdout
        # is only the agent's response. Overridable because this is best-effort and unverified
        # across CLI versions; the prompt itself is always injected right after ``-p``.
        self._extra_args = extra_args if extra_args is not None else ["-s"]

    @classmethod
    def is_installed(cls, command: str = "copilot") -> bool:
        return shutil.which(command) is not None

    async def complete(self, prompt: str) -> str:
        # The prompt is the value of ``-p`` and MUST immediately follow it; extra flags (e.g.
        # ``-s``) come after. Delivered as an argument, never on stdin (see class docstring).
        # ``bypass_windows_shim`` routes a Windows ``copilot.CMD`` shim through node directly so
        # the >8 KB prompt argv clears cmd.exe's 8191-char limit (see :func:`_run_host_cli`).
        #
        # Self-observation break (borrow-host loop fix): every ``copilot -p`` creates a fresh
        # ``~/.copilot/session-state/<id>/events.jsonl`` — the exact tree the daemon observes on
        # a Copilot box. We pin that id ourselves with ``--session-id <uuid>`` and register it as
        # an internal extraction session *before* spawning, so the poller's ``active_sessions``
        # skips it and can never re-observe (and re-extract from) our own extraction call.
        # Registering before the spawn guarantees the id is known before its ``events.jsonl`` can
        # exist, closing any poller race. Copilot-only: Claude writes to ``~/.claude`` (not the
        # observed Copilot tree), so :class:`ClaudeHostProcess` is intentionally left untouched.
        session_id = str(uuid.uuid4())
        internal_sessions.register(session_id)
        argv = ["--session-id", session_id, "-p", prompt, *self._extra_args]
        return await _run_host_cli(self._command, argv, bypass_windows_shim=True)


class ClaudeHostProcess:
    """Best-effort Claude Code CLI subprocess implementation of :class:`HostProcess`.

    Drives Anthropic's ``claude`` CLI non-interactively in print mode
    (``claude -p --output-format text``). Unlike :class:`CopilotHostProcess`, ``claude -p`` *does*
    read the prompt from **stdin**, so the prompt is fed there and ``argv`` carries only flags —
    this per-host divergence is exactly why the two hosts don't share a prompt-delivery path. Like
    the Copilot impl this is *best effort* — the exact headless invocation may vary by CLI version,
    so the real subprocess path is NOT exercised by the hermetic gate (which fakes ``HostProcess``).
    Availability is discovered via ``shutil.which`` so the strategy layer can fall back cleanly when
    Claude is not installed.
    """

    def __init__(self, command: str = "claude", extra_args: list[str] | None = None) -> None:
        self._command = command
        # ``-p/--print`` is Claude Code's non-interactive mode; ``--output-format text``
        # yields a plain-text completion (its default, stated explicitly so a user/global
        # config default cannot switch us to json/stream-json). Overridable because this
        # is best-effort and unverified across versions.
        self._extra_args = (
            extra_args if extra_args is not None else ["-p", "--output-format", "text"]
        )

    @classmethod
    def is_installed(cls, command: str = "claude") -> bool:
        return shutil.which(command) is not None

    async def complete(self, prompt: str) -> str:
        # ``claude -p`` reads the prompt from stdin; ``argv`` carries only the flags.
        return await _run_host_cli(
            self._command, list(self._extra_args), stdin_payload=prompt.encode("utf-8")
        )


class _UnknownHostProcess:
    """Fail-loud :class:`HostProcess` placeholder for an unregistered ``host``.

    The strategy layer contracts that constructing a client is cheap and never raises, so
    engine construction (and ``search()``/``health()``) keep working even for a misconfigured
    host. This placeholder honors that: construction is trivial, and the loud, actionable
    error surfaces only when graphiti actually calls :meth:`complete` at extraction time —
    never a silent fallback to a different host's protocol.
    """

    def __init__(self, host: str | None) -> None:
        self._host = host

    async def complete(self, prompt: str) -> str:
        raise HostProcessError(
            f"borrow-host: unknown host {self._host!r}; no HostProcess is registered "
            f"(known hosts: {sorted(HOST_PROCESSES)})"
        )


#: Registry mapping a provider *agent-id* (``LLM_HOST``, e.g. ``copilot``/``claude``) to its
#: :class:`HostProcess` implementation.
#:
#: NOTE on ``host`` semantics: although ``LLMStrategyHint.host`` is documented as "the host
#: CLI command whose model is borrowed", ``cfg.llm.host`` is used here as an **agent-id
#: registry key**, NOT a raw CLI command. Each :class:`HostProcess` subclass owns its own
#: default command (``copilot``/``claude`` coincide with their agent-ids but need not), and an
#: unregistered agent-id fails loud via :class:`_UnknownHostProcess` instead of being executed
#: as a command through another host's protocol (the original #87 bug).
HOST_PROCESSES: dict[str, type[HostProcess]] = {
    "copilot": CopilotHostProcess,
    "claude": ClaudeHostProcess,
}


def resolve_host_process(host: str | None) -> type[HostProcess] | None:
    """Return the :class:`HostProcess` class for agent-id ``host``, or ``None`` if unknown.

    A falsy ``host`` maps to the ``copilot`` default, preserving the historical
    ``cfg.llm.host or "copilot"`` behavior; a genuine unregistered agent-id returns ``None``
    so the strategy reports unavailability and builds a fail-loud client instead of silently
    treating it as Copilot.
    """
    return HOST_PROCESSES.get(host or "copilot")
