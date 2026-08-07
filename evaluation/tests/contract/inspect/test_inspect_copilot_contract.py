from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType

import pytest
from memrelay_eval.adapters.copilot.session import CopilotSdkSessionRuntime
from memrelay_eval.adapters.inspect.agent import CopilotSdkInspectAgent, build_inspect_custom_agent
from memrelay_eval.adapters.inspect.task import (
    InspectTaskRequest,
    LiveConformanceEnvelope,
    SessionLimits,
)
from memrelay_eval.domain.errors import ConformancePauseError


def _task() -> InspectTaskRequest:
    return InspectTaskRequest(
        "opaque-task",
        {"opaque": "metadata"},
        "synthetic task",
        "native-model",
        {"tools": True, "permissions": True},
        "high",
        "large",
        ("terminal",),
        ("read",),
        SessionLimits(30, 20, 100),
    )


class FakeEvent:
    def to_dict(self) -> dict[str, object]:
        return {
            "status": "succeeded",
            "event_references": ["event-1"],
            "patch_references": ["patch-1"],
            "usage": {"total_tokens": 9},
        }


class FakeSession:
    def __init__(self) -> None:
        self.prompt = ""
        self.timeout = 0.0
        self.disconnected = False

    async def send_and_wait(self, prompt: str, *, timeout: float) -> FakeEvent:
        self.prompt = prompt
        self.timeout = timeout
        return FakeEvent()

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeClient:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.options: dict[str, object] = {}
        self.session = FakeSession()

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def create_session(self, **options: object) -> FakeSession:
        self.options = options
        return self.session


def test_direct_sdk_session_preserves_locked_controls_and_native_evidence() -> None:
    client = FakeClient()
    record = asyncio.run(CopilotSdkSessionRuntime(lambda: client).execute(_task()))

    assert client.options == {
        "model": "native-model",
        "tools": ["terminal"],
        "permissions": ["read"],
        "reasoning_effort": "high",
        "context_tier": "large",
    }
    assert client.session.prompt == "synthetic task"
    assert client.session.timeout == 30
    assert client.session.disconnected and client.started and client.stopped
    assert record.state == "succeeded"
    assert record.event_references == ("event-1",)
    assert record.patch_references == ("patch-1",)
    assert record.usage["total_tokens"] == 9


def test_execution_adapters_have_no_alternate_inference_route() -> None:
    root = Path(__file__).parents[3] / "src" / "memrelay_eval" / "adapters"
    source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for directory in (root / "inspect", root / "copilot")
        for path in sorted(directory.glob("*.py"))
    )

    assert "openai" not in source
    assert "byok" not in source
    assert "providerconfig" not in source


def test_pinned_custom_agent_uses_the_official_decorator_surface(monkeypatch) -> None:
    module = ModuleType("inspect_ai")
    calls: list[object] = []

    def agent(factory):
        calls.append(factory)
        return factory

    module.agent = agent
    monkeypatch.setitem(sys.modules, "inspect_ai", module)
    monkeypatch.setattr(
        "memrelay_eval.adapters.inspect.agent.importlib.metadata.version",
        lambda name: "0.3.252",
    )

    factory = build_inspect_custom_agent(lambda task: _native(task))

    assert len(calls) == 1
    assert isinstance(factory(), CopilotSdkInspectAgent)


def test_real_sdk_session_refuses_without_explicit_finite_conformance_envelope() -> None:
    runtime = CopilotSdkSessionRuntime()

    with pytest.raises(ConformancePauseError, match="explicit finite"):
        asyncio.run(runtime.execute(_task()))

    assert LiveConformanceEnvelope("live_conformance", 1, 1.0, 100, 30, 30).session_limit == 1


async def _native(task: InspectTaskRequest):
    del task
    from memrelay_eval.adapters.inspect.task import NativeTerminalRecord

    return NativeTerminalRecord("succeeded", (), (), {})
