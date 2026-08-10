from __future__ import annotations

import pytest
from memrelay_eval.adapters.grader import executable
from memrelay_eval.domain.errors import NetworkSandboxUnavailableError


def test_unsupported_platform_fails_closed_before_grader_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable._network_sandbox_kind.cache_clear()
    monkeypatch.setattr(executable.sys, "platform", "win32")

    with pytest.raises(NetworkSandboxUnavailableError):
        executable._require_network_sandbox()
    executable._network_sandbox_kind.cache_clear()
