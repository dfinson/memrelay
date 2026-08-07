"""Domain-owned values that terminate all Inspect and SDK types at the adapter boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from memrelay_eval.domain.errors import ExecutionAdapterError

INSPECT_VERSION = "0.3.252"


@dataclass(frozen=True, slots=True)
class SessionLimits:
    """Explicit positive bounds carried unchanged from Inspect to the native session."""

    wall_seconds: float
    active_seconds: float
    token_limit: int

    def __post_init__(self) -> None:
        if self.wall_seconds <= 0 or self.active_seconds <= 0 or self.token_limit <= 0:
            raise ValueError("execution limits must be positive")


@dataclass(frozen=True, slots=True)
class LiveConformanceEnvelope:
    """Explicit finite authorization required before a real native SDK session."""

    operator_action: str
    session_limit: int
    credit_limit: float
    token_limit: int
    active_seconds_limit: float
    wall_seconds_limit: float

    def __post_init__(self) -> None:
        if self.operator_action != "live_conformance":
            raise ValueError("live native sessions require explicit live_conformance action")
        if (
            self.session_limit < 1
            or self.credit_limit <= 0
            or self.token_limit <= 0
            or self.active_seconds_limit <= 0
            or self.wall_seconds_limit <= 0
        ):
            raise ValueError("live conformance caps must be explicit positive values")


@dataclass(frozen=True, slots=True)
class InspectTaskRequest:
    """An opaque task request scheduled by Inspect, never by an inference adapter."""

    task_id: str
    metadata: Mapping[str, object]
    prompt: str
    model_id: str
    capabilities: Mapping[str, object]
    reasoning_effort: object
    context_tier: object
    tools: tuple[str, ...]
    permissions: tuple[str, ...]
    limits: SessionLimits

    def __post_init__(self) -> None:
        if not self.task_id or not self.model_id or not self.prompt:
            raise ValueError("task identity, model identity, and task body are required")
        if not self.tools or not self.permissions:
            raise ValueError("native tool and permission controls are required")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities)))
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "permissions", tuple(self.permissions))


@dataclass(frozen=True, slots=True)
class NativeTerminalRecord:
    """The minimal native terminal projection required to corroborate Inspect."""

    state: str
    event_references: tuple[str, ...]
    patch_references: tuple[str, ...]
    usage: Mapping[str, int | float]
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if self.state not in {"succeeded", "failed", "cancelled", "timed_out"}:
            raise ExecutionAdapterError(
                "native_terminal_state_invalid", "native terminal state is invalid"
            )
        if self.state == "failed" and not self.failure_code:
            raise ExecutionAdapterError(
                "native_failure_code_missing", "failed native terminal lacks a type"
            )
        object.__setattr__(self, "event_references", tuple(self.event_references))
        object.__setattr__(self, "patch_references", tuple(self.patch_references))
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))
