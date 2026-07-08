"""Borrow-host LLM strategy (E4-S2 / #35).

``BorrowHostLLMClient`` implements graphiti-core's ``LLMClient`` without any API
key by *borrowing the host agent's own model*: it renders graphiti's structured
prompt to plain text, appends the requested ``response_model`` JSON schema, and
asks a host process to complete it, then robustly parses JSON back out.

The actual host inference call is isolated behind the small :class:`HostProcess`
protocol (``async complete(prompt) -> str``) so it can be faked in tests. The
real Copilot-subprocess implementation (:class:`CopilotHostProcess`) is
best-effort and MUST NOT be required for the hermetic gate.
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


class CopilotHostProcess:
    """Best-effort Copilot CLI subprocess implementation of :class:`HostProcess`.

    This wires borrow-host to a locally installed Copilot CLI by running it
    non-interactively with the prompt on stdin. It is intentionally *best
    effort*: the exact non-interactive invocation may vary by CLI version, so
    the real subprocess path is NOT exercised by the hermetic gate (which uses a
    deterministic mock). Availability is discovered via ``shutil.which`` so the
    strategy layer can fall back cleanly when Copilot is not installed.
    """

    def __init__(self, command: str = "copilot", extra_args: list[str] | None = None) -> None:
        self._command = command
        # ``-p`` runs the Copilot CLI in one-shot prompt mode; kept overridable
        # because this is best-effort and unverified across versions.
        self._extra_args = extra_args if extra_args is not None else ["-p"]

    @classmethod
    def is_installed(cls, command: str = "copilot") -> bool:
        return shutil.which(command) is not None

    async def complete(self, prompt: str) -> str:
        if shutil.which(self._command) is None:
            raise HostProcessError(f"host command {self._command!r} not found on PATH")
        try:
            process = await asyncio.create_subprocess_exec(
                self._command,
                *self._extra_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate(prompt.encode("utf-8"))
        except OSError as exc:  # pragma: no cover - environment dependent
            raise HostProcessError(f"failed to launch host process: {exc}") from exc
        if process.returncode != 0:
            raise HostProcessError(
                f"host process exited {process.returncode}: "
                f"{stderr.decode('utf-8', 'replace').strip()}"
            )
        return stdout.decode("utf-8", "replace")
