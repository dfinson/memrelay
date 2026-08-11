from __future__ import annotations

import json

import pytest
from memrelay_eval.adapters.copilot.judge_worker import _request_from_bytes
from memrelay_eval.domain.errors import JudgePanelConformanceError


def test_worker_rejects_malformed_or_unauthorized_tool_request() -> None:
    malformed = {
        "session_id": "session-a",
        "candidate_id": "candidate-a",
        "model_id": "judge-a",
        "reasoning_effort": "high",
        "context_tier": "large",
        "system_prompt": "judge",
        "rubric_sha256": "a" * 64,
        "tools": [{"name": "read_blinded_artifact", "read_only": True}],
        "decoding_controls": {"session_defaults": "github-copilot-sdk-1.0.8"},
        "authorized_blinded_artifacts": {"not-a-location": "evidence"},
        "view": "{}",
        "wall_seconds_limit": 10,
    }

    with pytest.raises(JudgePanelConformanceError, match="process request invalid"):
        _request_from_bytes(json.dumps(malformed).encode("utf-8"))
