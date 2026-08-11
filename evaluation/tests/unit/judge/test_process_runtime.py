from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from memrelay_eval.adapters.process.environment import (
    CredentialDomain,
    CredentialReference,
    ProcessRole,
)
from memrelay_eval.adapters.process.judge import IsolatedJudgeProcessRuntime
from memrelay_eval.scoring.rubric import JudgeSessionRequest


def _request() -> JudgeSessionRequest:
    return JudgeSessionRequest(
        session_id="judge-session-a",
        candidate_id="candidate-a",
        model_id="judge-one",
        reasoning_effort="high",
        context_tier="large",
        system_prompt="Judge blinded evidence only.",
        rubric_sha256="a" * 64,
        tools=(
            {
                "name": "read_blinded_artifact",
                "read_only": True,
                "input_schema": {"type": "object"},
            },
        ),
        decoding_controls={"temperature": 0, "top_p": 1},
        view_bytes=b'{"schema_version":"1.0.0"}',
        wall_seconds_limit=10,
    )


class _Launcher:
    def __init__(self) -> None:
        self.requests = []

    def execute(self, request: object) -> object:
        self.requests.append(request)
        result_path = Path(request.command[-1])
        result_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "response": None,
                    "tokens": 0,
                    "tool_calls": 0,
                    "active_seconds": 0,
                    "wall_seconds": 0,
                    "failure_code": "judge_provider_unavailable",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(exit=SimpleNamespace(outcome="exited", returncode=0))


def test_isolated_runtime_serializes_only_blinded_input_to_a_judge_allowlist(
    tmp_path: Path,
) -> None:
    launcher = _Launcher()
    runtime = IsolatedJudgeProcessRuntime(
        launcher,
        tmp_path,
        {"PATH": "path", "SYSTEMROOT": "system"},
        (CredentialReference("COPILOT_AUTH_TOKEN", CredentialDomain.COPILOT, ProcessRole.JUDGE),),
        {"COPILOT_AUTH_TOKEN": "host-auth"},
    )

    result = asyncio.run(runtime.run_judge_session(_request()))

    assert result.status == "failed"
    assert result.failure_code == "judge_provider_unavailable"
    assert len(launcher.requests) == 1
    request = launcher.requests[0]
    assert request.role is ProcessRole.JUDGE
    assert set(request.environment) == {"PATH", "SYSTEMROOT", "COPILOT_AUTH_TOKEN"}
    assert "OPENAI_API_KEY" not in request.environment
    assert request.command[1:3] == ("-m", "memrelay_eval.adapters.copilot.judge_worker")
