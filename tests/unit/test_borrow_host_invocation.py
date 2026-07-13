"""Hermetic conformance tests for borrow-host CLI *invocation shape* (Wall A / #35).

These do NOT spawn a real CLI, touch the network, or burn AI credits: they patch
``asyncio.create_subprocess_exec`` and ``shutil.which`` inside
``memrelay.engine.llm.borrow_host`` and assert exactly *how* each host is invoked —

* the **resolved** command path (e.g. Windows ``copilot.CMD``) reaches
  ``create_subprocess_exec`` (never the bare ``copilot``/``claude`` name), and
* the prompt is delivered per host: Copilot as the ``-p`` **argument** (never stdin),
  Claude on **stdin** (never argv).

The ``test_counterfactual_*`` case pins the two origin/main defects, so it *fails* against
the pre-fix code and passes only once the per-host wiring is correct.
"""

from __future__ import annotations

import asyncio

import pytest

from memrelay.engine.llm import borrow_host
from memrelay.engine.llm.borrow_host import (
    ClaudeHostProcess,
    CopilotHostProcess,
    HostProcessError,
)


class _FakeProcess:
    """Stand-in for the object ``create_subprocess_exec`` awaits into."""

    def __init__(
        self,
        *,
        stdout: bytes = b'{"nodes": []}',
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.communicated = False
        self.stdin_payload: bytes | None = None

    async def communicate(self, payload: bytes | None = None) -> tuple[bytes, bytes]:
        self.communicated = True
        self.stdin_payload = payload
        return self._stdout, self._stderr


class _ExecRecorder:
    """Async stand-in for ``asyncio.create_subprocess_exec`` that records its one call."""

    def __init__(self, process: _FakeProcess) -> None:
        self._process = process
        self.args: tuple = ()
        self.kwargs: dict = {}
        self.call_count = 0

    async def __call__(self, *args: object, **kwargs: object) -> _FakeProcess:
        self.args = args
        self.kwargs = kwargs
        self.call_count += 1
        return self._process


def _patch(monkeypatch: pytest.MonkeyPatch, process: _FakeProcess) -> _ExecRecorder:
    """Patch the two outside-world seams and return the exec recorder."""
    recorder = _ExecRecorder(process)
    monkeypatch.setattr(borrow_host.asyncio, "create_subprocess_exec", recorder)
    # Simulate Windows PATHEXT resolution: ``which`` returns a ``.CMD`` absolute path —
    # exactly the value that must reach ``create_subprocess_exec`` (the bare name would
    # raise WinError 2 because exec does no PATHEXT lookup).
    monkeypatch.setattr(borrow_host.shutil, "which", lambda cmd: rf"C:\fake\bin\{cmd}.CMD")
    return recorder


def test_copilot_uses_resolved_path_and_prompt_as_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess()
    recorder = _patch(monkeypatch, process)
    prompt = "EXTRACT-ENTITIES-CANARY"

    out = asyncio.run(CopilotHostProcess().complete(prompt))

    # (a) the RESOLVED .CMD path reaches exec — not the bare "copilot".
    assert recorder.args[0] == r"C:\fake\bin\copilot.CMD"
    # (b) full argv is the proven probe-C shape: prompt is the -p ARGUMENT, then -s.
    assert list(recorder.args) == [r"C:\fake\bin\copilot.CMD", "-p", prompt, "-s"]
    # the prompt sits immediately after -p (it is that flag's value).
    argv = list(recorder.args)
    assert argv[argv.index("-p") + 1] == prompt
    # (b) and it is NOT delivered on stdin.
    assert process.stdin_payload is None
    # rc==0 path returns stdout verbatim (JSON is parsed by the client layer, not here).
    assert out == '{"nodes": []}'


def test_claude_uses_resolved_path_and_prompt_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess()
    recorder = _patch(monkeypatch, process)
    prompt = "CLAUDE-STDIN-CANARY"

    asyncio.run(ClaudeHostProcess().complete(prompt))

    # (a) the RESOLVED .CMD path reaches exec — not the bare "claude".
    assert recorder.args[0] == r"C:\fake\bin\claude.CMD"
    # (c) argv carries only flags (unchanged); the prompt is NOT an argv token.
    assert list(recorder.args) == [r"C:\fake\bin\claude.CMD", "-p", "--output-format", "text"]
    assert prompt not in recorder.args
    # (c) the prompt is delivered on stdin.
    assert process.stdin_payload == prompt.encode("utf-8")


def test_both_hosts_forward_the_windows_cmd_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicit Windows coverage: whatever ``which`` resolves (here .CMD) is what exec receives,
    # for BOTH hosts (the WinError-2 defect hit Copilot and Claude alike).
    for host in (CopilotHostProcess(), ClaudeHostProcess()):
        process = _FakeProcess()
        recorder = _patch(monkeypatch, process)
        asyncio.run(host.complete("x"))
        assert recorder.args[0].endswith(".CMD")


def test_nonzero_exit_raises_hostprocesserror(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess(stdout=b"", stderr=b"boom", returncode=1)
    _patch(monkeypatch, process)
    with pytest.raises(HostProcessError) as excinfo:
        asyncio.run(CopilotHostProcess().complete("x"))
    assert "exited 1" in str(excinfo.value)
    assert "boom" in str(excinfo.value)


def test_missing_binary_raises_before_exec(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``which`` returns None (not installed): the runner must fail loud WITHOUT ever
    # reaching ``create_subprocess_exec``.
    process = _FakeProcess()
    recorder = _ExecRecorder(process)
    monkeypatch.setattr(borrow_host.asyncio, "create_subprocess_exec", recorder)
    monkeypatch.setattr(borrow_host.shutil, "which", lambda cmd: None)
    with pytest.raises(HostProcessError):
        asyncio.run(CopilotHostProcess().complete("x"))
    assert recorder.call_count == 0


def test_counterfactual_pins_the_origin_main_defects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails against origin/main; passes only with the per-host fix.

    origin/main ran ``create_subprocess_exec("copilot", "-p")`` (bare name → WinError 2 on
    Windows) and wrote the prompt to **stdin** (Copilot's ``-p`` ignores stdin → rc=1
    "argument missing"). Each assertion below is exactly one of those defects, inverted.
    """
    process = _FakeProcess()
    recorder = _patch(monkeypatch, process)
    prompt = "COUNTERFACTUAL-CANARY"

    asyncio.run(CopilotHostProcess().complete(prompt))

    # Defect 1 (WinError 2): the bare name must NOT be what exec receives.
    assert recorder.args[0] != "copilot"
    assert recorder.args[0].endswith(".CMD")
    # Defect 2 (arg-missing): the prompt must be an argv argument, NOT on stdin.
    assert prompt in recorder.args
    assert process.stdin_payload != prompt.encode("utf-8")
    assert process.stdin_payload is None
