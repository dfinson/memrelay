"""Child entry point for the isolated host-authenticated judge process."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from memrelay_eval.adapters.copilot.session import CopilotSdkSessionRuntime
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.errors import ConformancePauseError, JudgePanelConformanceError
from memrelay_eval.scoring.rubric import JudgeRuntimeResult, JudgeSessionRequest


def main(argv: list[str] | None = None) -> int:
    arguments = argv or sys.argv[1:]
    if len(arguments) != 2:
        return 2
    request_path, result_path = (Path(argument) for argument in arguments)
    try:
        request = _request_from_bytes(request_path.read_bytes())
    except (OSError, JudgePanelConformanceError, UnicodeDecodeError, json.JSONDecodeError):
        return 2
    result = asyncio.run(_run(request))
    result_path.write_bytes(canonical_bytes(_result_document(result)))
    return 0


async def _run(request: JudgeSessionRequest) -> JudgeRuntimeResult:
    try:
        result = await CopilotSdkSessionRuntime().run_session(request)
    except ConformancePauseError as error:
        return JudgeRuntimeResult("failed", None, failure_code=error.code)
    if not isinstance(result, JudgeRuntimeResult):
        return JudgeRuntimeResult(
            "failed", None, failure_code="judge_worker_runtime_result_invalid"
        )
    return result


def _request_from_bytes(data: bytes) -> JudgeSessionRequest:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, Mapping):
        raise JudgePanelConformanceError("judge_process_request_invalid")
    view = value.get("view")
    tools = value.get("tools")
    controls = value.get("decoding_controls")
    if (
        not isinstance(view, str)
        or not isinstance(tools, list)
        or not isinstance(controls, Mapping)
    ):
        raise JudgePanelConformanceError("judge_process_request_invalid")
    return JudgeSessionRequest(
        session_id=value.get("session_id"),
        candidate_id=value.get("candidate_id"),
        model_id=value.get("model_id"),
        reasoning_effort=value.get("reasoning_effort"),
        context_tier=value.get("context_tier"),
        system_prompt=value.get("system_prompt"),
        rubric_sha256=value.get("rubric_sha256"),
        tools=tuple(tool for tool in tools if isinstance(tool, Mapping)),
        decoding_controls=controls,
        view_bytes=view.encode("utf-8"),
        wall_seconds_limit=value.get("wall_seconds_limit"),
    )


def _result_document(result: JudgeRuntimeResult) -> dict[str, object]:
    return {
        "status": result.status,
        "response": dict(result.response) if result.response is not None else None,
        "tokens": result.tokens,
        "tool_calls": result.tool_calls,
        "active_seconds": result.active_seconds,
        "wall_seconds": result.wall_seconds,
        "failure_code": result.failure_code,
    }


if __name__ == "__main__":
    raise SystemExit(main())
