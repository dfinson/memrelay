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
    NativeTerminalRecord,
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
    def __init__(self, payload: object | None = None) -> None:
        self._payload = payload

    def to_dict(self) -> object:
        return (
            self._payload
            if self._payload is not None
            else {
                "status": "succeeded",
                "event_references": ["event-1"],
                "patch_references": ["patch-1"],
                "usage": {"total_tokens": 9},
            }
        )


class FakeSession:
    def __init__(self, event: FakeEvent | None = None) -> None:
        self.prompt = ""
        self.timeout = 0.0
        self.disconnected = False
        self._event = event or FakeEvent()

    async def send_and_wait(self, prompt: str, *, timeout: float) -> FakeEvent:
        self.prompt = prompt
        self.timeout = timeout
        return self._event

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeClient:
    def __init__(self, event: FakeEvent | None = None) -> None:
        self.started = False
        self.stopped = False
        self.options: dict[str, object] = {}
        self.session = FakeSession(event)

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
    assert record.raw_event_payload is not None
    assert record.raw_event_payload["event_references"] == ["event-1"]


@pytest.mark.parametrize(
    ("payload", "failure_code"),
    (
        (
            {"event_references": ["event-1"], "patch_references": [], "usage": {"total_tokens": 9}},
            "native_terminal_status_missing",
        ),
        (
            {
                "status": None,
                "event_references": ["event-1"],
                "patch_references": [],
                "usage": {"total_tokens": 9},
            },
            "native_terminal_status_null",
        ),
        (
            {
                "status": "",
                "event_references": ["event-1"],
                "patch_references": [],
                "usage": {"total_tokens": 9},
            },
            "native_terminal_status_invalid",
        ),
        (
            {
                "status": "unknown",
                "event_references": ["event-1"],
                "patch_references": [],
                "usage": {"total_tokens": 9},
            },
            "native_terminal_status_unknown",
        ),
        (
            {
                "status": "succeeded",
                "event_references": ["event-1"],
                "patch_references": "patch-1",
                "usage": {"total_tokens": 9},
            },
            "native_terminal_fields_invalid",
        ),
    ),
)
def test_direct_sdk_session_fails_closed_for_malformed_terminal_payload(
    payload: dict[str, object], failure_code: str
) -> None:
    client = FakeClient(FakeEvent(payload))

    runtime = CopilotSdkSessionRuntime(lambda: client)
    record = asyncio.run(runtime.execute(_task()))

    assert record.state == "failed"
    assert record.failure_code == failure_code
    assert record.corroborates_inspect is False
    assert record.event_references == ("event-1",)
    assert record.usage == {"total_tokens": 9}


def test_direct_sdk_session_fails_closed_for_a_nonmapping_native_terminal_payload() -> None:
    client = FakeClient(FakeEvent([]))

    record = asyncio.run(CopilotSdkSessionRuntime(lambda: client).execute(_task()))

    assert record.state == "failed"
    assert record.failure_code == "native_terminal_payload_invalid"
    assert record.corroborates_inspect is False


@pytest.mark.parametrize(
    "status",
    ("succeeded", "cancelled", "timed_out", "failed"),
)
def test_direct_sdk_session_preserves_each_valid_native_terminal_status(status: str) -> None:
    payload: dict[str, object] = {
        "status": status,
        "event_references": [],
        "patch_references": [],
        "usage": {},
    }
    if status == "failed":
        payload["failure_code"] = "native_failed"

    record = asyncio.run(
        CopilotSdkSessionRuntime(lambda: FakeClient(FakeEvent(payload))).execute(_task())
    )

    assert record.state == status
    assert record.corroborates_inspect is True


@pytest.mark.parametrize(
    "failure_code",
    (
        "native_terminal_status_missing",
        "native_terminal_status_null",
        "native_terminal_status_unknown",
        "native_terminal_payload_invalid",
    ),
)
def test_inspect_agent_preserves_a_typed_noncorroborating_terminal_failure(
    failure_code: str,
) -> None:
    malformed = NativeTerminalRecord(
        "failed",
        ("event-1",),
        (),
        {},
        failure_code,
        corroborates_inspect=False,
    )
    agent = CopilotSdkInspectAgent(lambda task: _native_record(task, malformed))

    result = asyncio.run(agent.execute(_task()))

    assert result == malformed


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


async def _native_record(
    task: InspectTaskRequest, record: NativeTerminalRecord
) -> NativeTerminalRecord:
    del task
    return record
