"""Official Inspect custom-agent entry point backed only by the official Copilot SDK."""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from memrelay_eval.adapters.inspect.task import (
    INSPECT_VERSION,
    InspectTaskRequest,
    NativeTerminalRecord,
)
from memrelay_eval.domain.errors import ConformancePauseError, ExecutionAdapterError

NativeSession = Callable[[InspectTaskRequest], Awaitable[NativeTerminalRecord]]


@dataclass(slots=True)
class CopilotSdkInspectAgent:
    """Thin custom agent: Inspect schedules it while the native SDK performs inference."""

    native_session: NativeSession

    async def execute(self, task: InspectTaskRequest) -> NativeTerminalRecord:
        try:
            return await self.native_session(task)
        except ExecutionAdapterError:
            raise
        except TimeoutError as error:
            raise ExecutionAdapterError("sdk_timeout", "native session timed out") from error
        except asyncio.CancelledError as error:
            raise ExecutionAdapterError("sdk_cancelled", "native session was cancelled") from error
        except Exception as error:
            raise ExecutionAdapterError(
                "sdk_crash", "native session terminated unexpectedly"
            ) from error


def build_inspect_custom_agent(native_session: NativeSession) -> object:
    """Register the exact pinned Inspect custom-agent surface without model routing."""

    _require_pinned_inspect()
    module = importlib.import_module("inspect_ai")
    decorator = getattr(module, "agent", None)
    if callable(decorator):
        return decorator(lambda: CopilotSdkInspectAgent(native_session))
    bridge = getattr(module, "agent_bridge", None)
    if callable(bridge):
        return bridge(lambda: CopilotSdkInspectAgent(native_session))
    raise ConformancePauseError(
        "inspect_custom_agent_api_missing",
        "Inspect 0.3.252 does not expose its documented custom-agent API",
    )


def _require_pinned_inspect() -> None:
    try:
        installed = importlib.metadata.version("inspect-ai")
    except importlib.metadata.PackageNotFoundError as error:
        raise ConformancePauseError(
            "inspect_unavailable", "inspect-ai 0.3.252 is required for execution"
        ) from error
    if installed != INSPECT_VERSION:
        raise ConformancePauseError(
            "inspect_version_mismatch",
            "inspect-ai version does not match the frozen execution lock",
        )
