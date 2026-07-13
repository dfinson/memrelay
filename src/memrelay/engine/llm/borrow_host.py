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
import shutil
from typing import Any, Protocol, runtime_checkable

from graphiti_core.llm_client.client import LLMClient, ModelSize
from graphiti_core.llm_client.config import LLMConfig as GraphitiLLMConfig
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

logger = logging.getLogger(__name__)

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


async def _run_host_cli(
    command: str,
    argv: list[str],
    *,
    stdin_payload: bytes | None = None,
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

    Kept out of the hermetic gate for the *real* subprocess on purpose (the exact CLI is
    environment- and version-dependent), but the *invocation shape* — resolved path, argv, and
    stdin-vs-arg prompt delivery — is asserted hermetically (patching ``create_subprocess_exec``
    and ``shutil.which``) in ``tests/unit/test_borrow_host_invocation.py``.
    """
    resolved = shutil.which(command)
    if resolved is None:
        raise HostProcessError(f"host command {command!r} not found on PATH")
    try:
        process = await asyncio.create_subprocess_exec(
            resolved,
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(stdin_payload)
    except OSError as exc:  # pragma: no cover - environment dependent
        raise HostProcessError(f"failed to launch host process: {exc}") from exc
    if process.returncode != 0:
        raise HostProcessError(
            f"host process exited {process.returncode}: {stderr.decode('utf-8', 'replace').strip()}"
        )
    return stdout.decode("utf-8", "replace")


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
        argv = ["-p", prompt, *self._extra_args]
        return await _run_host_cli(self._command, argv)


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
