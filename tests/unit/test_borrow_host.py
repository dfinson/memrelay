"""Unit tests for the borrow-host LLM client (E4-S2 / #35).

Covers schema-in-prompt, robust JSON parsing (fences / trailing prose), the
retry loop, and the fakeable ``HostProcess`` seam — all without a real Copilot
subprocess.
"""

from __future__ import annotations

import asyncio

import pytest
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from memrelay.engine.llm.borrow_host import (
    BorrowHostLLMClient,
    CopilotHostProcess,
    HostProcessError,
    _loads_json_object,
)


class _Sample(BaseModel):
    foo: int
    bar: str


class FakeHost:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_schema_is_appended_to_prompt():
    host = FakeHost(['{"foo": 1, "bar": "x"}'])
    client = BorrowHostLLMClient(host)
    out = asyncio.run(
        client._generate_response(
            [Message(role="user", content="extract entities")],
            response_model=_Sample,
        )
    )
    assert out == {"foo": 1, "bar": "x"}
    # The model's JSON schema (its field names) must be embedded in the prompt.
    assert "foo" in host.prompts[0]
    assert "bar" in host.prompts[0]


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Sure, here you go:\n{"a": 1}\nHope that helps!',
        '{"a": 1}',
    ],
)
def test_loads_json_object_is_robust(raw):
    assert _loads_json_object(raw) == {"a": 1}


def test_loads_json_object_rejects_non_object():
    with pytest.raises(ValueError):
        _loads_json_object("not json at all")


def test_retries_then_succeeds():
    host = FakeHost(["not valid json", '{"a": 2}'])
    client = BorrowHostLLMClient(host, max_json_retries=2)
    out = asyncio.run(client._generate_response([Message(role="user", content="x")]))
    assert out == {"a": 2}
    assert len(host.prompts) == 2  # one retry happened
    assert "not valid JSON" in host.prompts[1]  # corrective nudge included


def test_raises_after_exhausting_retries():
    host = FakeHost(["nope", "still nope", "argh"])
    client = BorrowHostLLMClient(host, max_json_retries=2)
    with pytest.raises(HostProcessError):
        asyncio.run(client._generate_response([Message(role="user", content="x")]))


def test_copilot_host_process_availability_is_bool():
    assert isinstance(CopilotHostProcess.is_installed("definitely-not-a-real-binary-xyz"), bool)


def test_copilot_host_process_reports_missing_binary():
    host = CopilotHostProcess(command="definitely-not-a-real-binary-xyz")
    with pytest.raises(HostProcessError):
        asyncio.run(host.complete("hello"))
