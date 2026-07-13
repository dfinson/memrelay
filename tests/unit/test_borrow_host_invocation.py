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
import logging
import sys

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


# ---------------------------------------------------------------------------
# Windows cmd.exe command-line overflow fix (#… borrow-host node-direct launch)
#
# On Windows ``shutil.which("copilot")`` resolves to an npm ``copilot.CMD`` shim. Executing a
# ``.CMD`` runs it under cmd.exe, whose command line is capped at 8191 chars — Graphiti extraction
# prompts are >8 KB, so every real call died with "The command line is too long." before Copilot
# launched. The fix launches ``node <npm-loader.js> *argv`` (CreateProcess, 32767-char cap) for the
# Windows-shim Copilot case only; Claude (prompt on stdin) and non-Windows/non-shim paths are
# untouched.
# ---------------------------------------------------------------------------


def test_windows_cmd_shim_redirects_copilot_through_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows ``copilot.CMD`` → launched as ``node <loader.js> -p <prompt> -s`` (not the shim)."""
    process = _FakeProcess()
    recorder = _ExecRecorder(process)
    monkeypatch.setattr(borrow_host.asyncio, "create_subprocess_exec", recorder)
    monkeypatch.setattr(borrow_host.sys, "platform", "win32")
    # ``copilot`` resolves to the .CMD shim; ``node`` resolves to a distinct real interpreter path.
    monkeypatch.setattr(
        borrow_host.shutil,
        "which",
        lambda cmd: r"C:\nodedir\node.exe" if cmd == "node" else rf"C:\fake\bin\{cmd}.CMD",
    )
    # The shim file itself isn't on disk (parse falls back); the conventional loader "exists".
    monkeypatch.setattr(borrow_host.os.path, "isfile", lambda p: p.endswith("npm-loader.js"))
    prompt = "EXTRACT-ENTITIES-CANARY"

    out = asyncio.run(CopilotHostProcess().complete(prompt))

    # program is node (from which('node')), NOT the .CMD shim.
    assert recorder.args[0] == r"C:\nodedir\node.exe"
    assert not recorder.args[0].lower().endswith(".cmd")
    # first arg is the npm loader .js, then the unchanged Copilot argv.
    assert recorder.args[1].endswith("npm-loader.js")
    assert list(recorder.args[2:]) == ["-p", prompt, "-s"]
    # prompt is still the -p argument, never stdin.
    assert process.stdin_payload is None
    assert out == '{"nodes": []}'


def test_extract_loader_from_shim_parses_npm_cmd(tmp_path) -> None:
    """The parser pulls the ``.js`` loader out of a real npm ``.cmd`` shim, resolving ``%dp0%``."""
    if sys.platform != "win32":
        pytest.skip("shim path resolution uses Windows separators")
    shim = tmp_path / "copilot.cmd"
    # The operative last line of npm's generated shim (%dp0% == the shim's own directory).
    shim.write_text(
        "@ECHO off\r\nGOTO start\r\n:start\r\nSETLOCAL\r\n"
        "endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "
        '"%_prog%"  "%dp0%\\node_modules\\@github\\copilot\\npm-loader.js" %*\r\n',
        encoding="utf-8",
    )

    loader = borrow_host._extract_loader_from_shim(str(shim))

    expected = str(tmp_path / "node_modules" / "@github" / "copilot" / "npm-loader.js")
    assert loader == expected


def test_non_windows_copilot_execs_resolved_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off Windows the resolved copilot path is exec'd directly — no node indirection."""
    process = _FakeProcess()
    recorder = _ExecRecorder(process)
    monkeypatch.setattr(borrow_host.asyncio, "create_subprocess_exec", recorder)
    monkeypatch.setattr(borrow_host.sys, "platform", "linux")
    monkeypatch.setattr(borrow_host.shutil, "which", lambda cmd: "/usr/bin/copilot")
    prompt = "LINUX-DIRECT-CANARY"

    asyncio.run(CopilotHostProcess().complete(prompt))

    assert list(recorder.args) == ["/usr/bin/copilot", "-p", prompt, "-s"]
    assert process.stdin_payload is None


def test_windows_non_shim_copilot_execs_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Windows ``copilot.exe`` (real executable, not a shim) is exec'd directly."""
    process = _FakeProcess()
    recorder = _ExecRecorder(process)
    monkeypatch.setattr(borrow_host.asyncio, "create_subprocess_exec", recorder)
    monkeypatch.setattr(borrow_host.sys, "platform", "win32")
    monkeypatch.setattr(borrow_host.shutil, "which", lambda cmd: r"C:\tools\copilot.exe")
    prompt = "WIN-EXE-DIRECT-CANARY"

    asyncio.run(CopilotHostProcess().complete(prompt))

    assert list(recorder.args) == [r"C:\tools\copilot.exe", "-p", prompt, "-s"]


def test_windows_shim_without_loader_falls_back_to_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no loader/node can be found, fall back to exec'ing the shim directly (never worse)."""
    process = _FakeProcess()
    recorder = _ExecRecorder(process)
    monkeypatch.setattr(borrow_host.asyncio, "create_subprocess_exec", recorder)
    monkeypatch.setattr(borrow_host.sys, "platform", "win32")
    monkeypatch.setattr(borrow_host.shutil, "which", lambda cmd: r"C:\fake\bin\copilot.CMD")
    monkeypatch.setattr(borrow_host.os.path, "isfile", lambda p: False)  # loader absent everywhere
    prompt = "FALLBACK-DIRECT-CANARY"

    asyncio.run(CopilotHostProcess().complete(prompt))

    # unchanged pre-fix behavior: the resolved .CMD reaches exec with the same argv.
    assert list(recorder.args) == [r"C:\fake\bin\copilot.CMD", "-p", prompt, "-s"]


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe 8191-char cap is Windows-specific")
def test_os_level_large_prompt_survives_node_direct(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """★ Real OS launch: a ~20 KB ``-p`` argument reaches the child via CreateProcess, no overflow.

    A real stand-in for node+loader (``sys.executable`` running a script that echoes the length of
    the ``-p`` value) is injected and launched through the *real* ``create_subprocess_exec`` — a
    genuine OS process, NOT cmd.exe and NOT copilot, so this spends zero AI quota and hits no
    network. It proves node-direct clears the 8191 limit that kills the ``.cmd`` path.
    """
    echo = tmp_path / "echo_len.py"
    echo.write_text(
        "import sys\ni = sys.argv.index('-p')\nsys.stdout.write(str(len(sys.argv[i + 1])))\n",
        encoding="utf-8",
    )
    # ``which`` resolves to a REAL .cmd shim. With the fix it is never executed (the injected
    # node stand-in runs instead); if the node-direct branch is reverted, _run_host_cli execs this
    # .cmd with the 20 KB argv and cmd.exe overflows — that is the revert→fail signal.
    shim = tmp_path / "copilot.cmd"
    shim.write_text("@echo off\r\necho stub-ran\r\n", encoding="utf-8")
    monkeypatch.setattr(borrow_host.shutil, "which", lambda cmd: str(shim))
    monkeypatch.setattr(
        borrow_host, "_node_shim_launch", lambda resolved: (sys.executable, str(echo))
    )
    prompt = "A" * 20000  # >> cmd.exe's 8191-char command-line limit

    out = asyncio.run(CopilotHostProcess().complete(prompt))

    # No HostProcessError was raised, and the child received the FULL 20 000-char argument.
    assert out.strip() == str(len(prompt))


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe 8191-char cap is Windows-specific")
def test_os_level_large_prompt_overflows_through_cmd_shim(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Counterfactual: the same ~20 KB prompt through a real ``.bat`` overflows (pre-fix path).

    This is what Copilot did before the fix — exec the ``.cmd``/``.bat`` directly. cmd.exe parses
    the now >8191-char command line and aborts *before* the stub body runs, raising the exact
    production error. It proves the length test has teeth and that node-direct is the fix.
    """
    stub = tmp_path / "copilot_stub.bat"
    stub.write_text("@echo off\r\necho stub-ran\r\n", encoding="utf-8")
    monkeypatch.setattr(borrow_host.shutil, "which", lambda cmd: str(stub))
    prompt = "A" * 20000

    # bypass_windows_shim defaults to False here → direct exec of the .bat, the pre-fix path.
    with pytest.raises(HostProcessError) as excinfo:
        asyncio.run(borrow_host._run_host_cli("copilot", ["-p", prompt, "-s"]))

    assert "command line is too long" in str(excinfo.value).lower()


def test_shim_fallback_warning_throttled_once_per_process(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The shim-bypass fallback warns at most once per process, not once per extraction call.

    ``_node_shim_launch`` runs on every Copilot completion, so a persistent misconfiguration (shim
    present but loader absent) must not flood the daemon log. Three consecutive fallbacks must emit
    a single WARNING, the later two dropping to DEBUG. Cross-platform: ``sys.platform`` and
    ``os.path.isfile`` are patched and nothing is launched.
    """
    monkeypatch.setattr(borrow_host, "_warned_shim_fallbacks", set())
    monkeypatch.setattr(borrow_host.sys, "platform", "win32")
    monkeypatch.setattr(borrow_host.os.path, "isfile", lambda _p: False)  # loader absent everywhere
    shim = r"C:\fake\bin\copilot.CMD"

    with caplog.at_level(logging.DEBUG, logger="memrelay.engine.llm.borrow_host"):
        assert borrow_host._node_shim_launch(shim) is None
        assert borrow_host._node_shim_launch(shim) is None
        assert borrow_host._node_shim_launch(shim) is None

    records = [r for r in caplog.records if "no node loader found" in r.getMessage()]
    warnings = [r for r in records if r.levelno == logging.WARNING]
    debugs = [r for r in records if r.levelno == logging.DEBUG]
    assert len(warnings) == 1  # three fallbacks -> exactly one warning
    assert len(debugs) == 2  # the remaining two dropped to debug
