from __future__ import annotations

import asyncio
from dataclasses import replace

from copilot import Tool, ToolInvocation
from memrelay_eval.adapters.copilot.session import CopilotSdkSessionRuntime
from memrelay_eval.scoring.rubric import JudgeSessionRequest


def _request(index: int) -> JudgeSessionRequest:
    return JudgeSessionRequest(
        session_id=f"session-{index}",
        candidate_id="candidate-a",
        model_id=f"judge-{index}",
        reasoning_effort="high",
        context_tier="large",
        system_prompt="Judge blinded evidence only.",
        rubric_sha256="a" * 64,
        tools=(
            {
                "name": "read_blinded_artifact",
                "description": "Read the supplied blinded artifact.",
                "read_only": True,
                "input_schema": {"type": "object"},
            },
        ),
        decoding_controls={"session_defaults": "github-copilot-sdk-1.0.8"},
        view_bytes=(
            b'{"schema_version":"1.0.0","source":{"artifact_id":"art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            b'"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},'
            b'"policy_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            b'"transform_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            b'"evidence":{},"artifact_locations":{}}'
        ),
        wall_seconds_limit=10,
    )


def _response() -> dict[str, object]:
    citation = f"artifact://blinded/{'b' * 64}"
    return {
        "status": "succeeded",
        "usage": {"total_tokens": 7, "tool_calls": 1},
        "structured_output": {
            "criteria": {
                criterion: {"score": 0.5, "uncertainty": 0.2, "citations": [citation]}
                for criterion in (
                    "uncovered_requirement_satisfaction",
                    "semantic_appropriateness",
                    "maintainability",
                    "unnecessary_complexity",
                    "repository_fit",
                    "evidence_supported_confidence",
                )
            }
        },
    }


class _Event:
    def to_dict(self) -> dict[str, object]:
        return _response()


class _Session:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.disconnected = False

    async def send_and_wait(self, prompt: str, timeout: float) -> _Event:
        assert timeout == 10
        self.prompts.append(prompt)
        return _Event()

    async def disconnect(self) -> None:
        self.disconnected = True


class _Client:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.options: list[dict[str, object]] = []
        self.sessions: list[_Session] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def create_session(self, **options: object) -> _Session:
        self.options.append(options)
        session = _Session()
        self.sessions.append(session)
        return session


def test_judge_runtime_creates_fresh_pinned_sdk_sessions_without_credential_options() -> None:
    clients: list[_Client] = []

    def factory() -> _Client:
        client = _Client()
        clients.append(client)
        return client

    runtime = CopilotSdkSessionRuntime(factory)
    results = [asyncio.run(runtime.run_session(_request(index))) for index in range(3)]

    assert [result.status for result in results] == ["completed"] * 3
    assert len(clients) == 3
    assert all(client.started and client.stopped for client in clients)
    assert all(client.sessions[0].disconnected for client in clients)
    assert all(isinstance(client.options[0]["tools"][0], Tool) for client in clients)
    assert all(client.options[0]["tools"][0].parameters == {"type": "object"} for client in clients)
    assert all("github_token" not in client.options[0] for client in clients)


def test_judge_runtime_rejects_success_without_native_usage_metering() -> None:
    class MissingUsageEvent:
        def to_dict(self) -> dict[str, object]:
            return {"status": "succeeded", "structured_output": _response()["structured_output"]}

    class MissingUsageSession(_Session):
        async def send_and_wait(self, prompt: str, timeout: float) -> MissingUsageEvent:
            del prompt, timeout
            return MissingUsageEvent()

    class MissingUsageClient(_Client):
        async def create_session(self, **options: object) -> MissingUsageSession:
            self.options.append(options)
            session = MissingUsageSession()
            self.sessions.append(session)
            return session

    result = asyncio.run(CopilotSdkSessionRuntime(MissingUsageClient).run_session(_request(1)))

    assert result.status == "failed"
    assert result.failure_code == "judge_native_usage_unavailable"


def test_judge_runtime_uses_sdk_tool_objects_and_denies_unapproved_artifacts() -> None:
    allowed = f"artifact://blinded/{'c' * 64}"

    class ToolShapeClient(_Client):
        async def create_session(self, **options: object) -> _Session:
            tool = options["tools"][0]
            assert isinstance(tool, Tool)
            assert tool.name == "read_blinded_artifact"
            assert tool.description == "Read the supplied blinded artifact."
            assert tool.parameters == {"type": "object"}
            allowed_result = tool.handler(
                ToolInvocation(
                    tool_name="read_blinded_artifact",
                    arguments={"location": allowed},
                )
            )
            denied_result = tool.handler(
                ToolInvocation(
                    tool_name="read_blinded_artifact",
                    arguments={"location": f"artifact://blinded/{'d' * 64}"},
                )
            )
            assert allowed_result.text_result_for_llm == "authorized evidence"
            assert allowed_result.tool_references == [allowed]
            assert denied_result.result_type == "denied"
            return await super().create_session(**options)

    request = replace(_request(1), authorized_blinded_artifacts={allowed: "authorized evidence"})
    result = asyncio.run(CopilotSdkSessionRuntime(ToolShapeClient).run_session(request))

    assert result.status == "completed"
