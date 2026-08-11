"""Disposable process transport for one blinded Copilot judge session."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path

from ...canonical import canonical_bytes
from ...domain.errors import JudgePanelConformanceError
from ...scoring.rubric import JudgeRuntimeResult, JudgeSessionRequest
from .environment import (
    CredentialReference,
    ProcessRole,
    build_process_environment,
)
from .launcher import (
    DisposableProcessLauncher,
    ProcessLaunchRequest,
)


class IsolatedJudgeProcessRuntime:
    """Start a unique child with only the JUDGE allowlist for every SDK assessment."""

    def __init__(
        self,
        launcher: DisposableProcessLauncher,
        worker_root: Path,
        runtime_environment: Mapping[str, str],
        credential_references: Sequence[CredentialReference],
        credential_values: Mapping[str, str],
    ) -> None:
        self._launcher = launcher
        self._worker_root = worker_root
        self._runtime_environment = dict(runtime_environment)
        self._credential_references = tuple(credential_references)
        self._credential_values = dict(credential_values)

    async def run_judge_session(self, session: object) -> object:
        """Serialize the blinded request to an owned child and return only typed terminal data."""

        if not isinstance(session, JudgeSessionRequest):
            raise JudgePanelConformanceError("judge_process_request_invalid")
        worker_dir = self._worker_root / sha256(session.session_id.encode("utf-8")).hexdigest()
        request_path = worker_dir / "request.json"
        result_path = worker_dir / "result.json"
        try:
            worker_dir.mkdir(parents=True, exist_ok=False)
            request_path.write_bytes(_request_bytes(session))
        except FileExistsError:
            return JudgeRuntimeResult(
                "failed", None, failure_code="judge_process_directory_conflict"
            )
        except OSError:
            return JudgeRuntimeResult("failed", None, failure_code="judge_process_io_failure")
        environment = build_process_environment(
            ProcessRole.JUDGE,
            runtime_environment=self._runtime_environment,
            credential_references=self._credential_references,
            credential_values=self._credential_values,
        )
        report = await asyncio.to_thread(
            self._launcher.execute,
            ProcessLaunchRequest(
                attempt_id=session.session_id,
                role=ProcessRole.JUDGE,
                command=(
                    sys.executable,
                    "-m",
                    "memrelay_eval.adapters.copilot.judge_worker",
                    str(request_path),
                    str(result_path),
                ),
                cwd=worker_dir,
                environment=environment,
                timeout_seconds=session.wall_seconds_limit,
            ),
        )
        if (
            report.exit.outcome != "exited"
            or report.exit.returncode != 0
            or not result_path.is_file()
        ):
            return JudgeRuntimeResult("failed", None, failure_code="judge_process_terminal_failure")
        try:
            return _result_from_bytes(result_path.read_bytes())
        except OSError:
            return JudgeRuntimeResult("failed", None, failure_code="judge_process_io_failure")


def _request_bytes(session: JudgeSessionRequest) -> bytes:
    return canonical_bytes(
        {
            "session_id": session.session_id,
            "candidate_id": session.candidate_id,
            "model_id": session.model_id,
            "reasoning_effort": session.reasoning_effort,
            "context_tier": session.context_tier,
            "system_prompt": session.system_prompt,
            "rubric_sha256": session.rubric_sha256,
            "tools": [dict(tool) for tool in session.tools],
            "decoding_controls": dict(session.decoding_controls),
            "authorized_blinded_artifacts": dict(session.authorized_blinded_artifacts),
            "view": session.view_bytes.decode("utf-8"),
            "wall_seconds_limit": session.wall_seconds_limit,
        },
    )


def _result_from_bytes(data: bytes) -> JudgeRuntimeResult:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JudgeRuntimeResult("failed", None, failure_code="judge_process_result_invalid")
    if not isinstance(value, Mapping):
        return JudgeRuntimeResult("failed", None, failure_code="judge_process_result_invalid")
    response = value.get("response")
    if response is not None and not isinstance(response, Mapping):
        return JudgeRuntimeResult("failed", None, failure_code="judge_process_result_invalid")
    try:
        return JudgeRuntimeResult(
            status=value.get("status"),
            response=response,
            tokens=value.get("tokens", 0),
            tool_calls=value.get("tool_calls", 0),
            active_seconds=value.get("active_seconds", 0.0),
            wall_seconds=value.get("wall_seconds", 0.0),
            failure_code=value.get("failure_code"),
        )
    except JudgePanelConformanceError:
        return JudgeRuntimeResult("failed", None, failure_code="judge_process_result_invalid")
