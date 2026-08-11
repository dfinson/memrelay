from __future__ import annotations

import asyncio
import base64
import inspect
import json
import shutil
import socket
import subprocess
import sys
import threading
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.adapters.grader import executable
from memrelay_eval.adapters.grader.executable import CredentialFreeExecutableGrader
from memrelay_eval.adapters.workspace.base import WorkspaceSnapshot
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.entities import GraderContract, GraderResult
from memrelay_eval.domain.errors import (
    GraderContractError,
    GraderReplayMismatchError,
    NetworkSandboxUnavailableError,
)
from memrelay_eval.domain.states import GraderTerminalKind
from memrelay_eval.evidence.required import require_unpaid_conformance_ports
from memrelay_eval.scoring.service import (
    classify_candidate_flakiness,
    compare_grading_replays,
    grade_with_bounded_regrades,
    require_intake_stability,
    require_matching_replays,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="OS network namespace unavailable; production grading fails closed",
)


def _files_document(files: dict[str, bytes]) -> bytes:
    return canonical_bytes(
        {
            "schema_version": "1.0.0",
            "files": [
                {
                    "path": path,
                    "mode": 0o644,
                    "sha256": sha256(contents).hexdigest(),
                    "content_base64": base64.b64encode(contents).decode("ascii"),
                }
                for path, contents in sorted(files.items())
            ],
        }
    )


def _snapshot(
    store: InMemoryArtifactStore,
    *,
    baseline: dict[str, bytes] | None = None,
    terminal: dict[str, bytes] | None = None,
) -> WorkspaceSnapshot:
    baseline = baseline or {"app.py": b"answer = 1\n"}
    terminal = terminal or {"app.py": b"answer = 2\n"}
    baseline_ref = store.put_bytes(
        _files_document(baseline),
        media_type="application/json",
        classification="workspace_snapshot",
    )
    terminal_ref = store.put_bytes(
        _files_document(terminal),
        media_type="application/json",
        classification="workspace_snapshot",
    )
    patch_ref = store.put_bytes(
        b"diff --git a/app.py b/app.py\n",
        media_type="text/x-diff",
        classification="workspace_patch",
    )
    document = canonical_bytes(
        {
            "schema_version": "1.0.0",
            "normalization": {
                "paths": "relative_posix",
                "timestamps": "omitted",
                "file_order": "codepoint",
                "reparse_points": "rejected",
            },
            "baseline_revision": "a" * 40,
            "terminal_revision": "a" * 40,
            "baseline_files_sha256": baseline_ref.sha256,
            "terminal_files_sha256": terminal_ref.sha256,
            "patch_sha256": patch_ref.sha256,
        }
    )
    canonical_ref = store.put_bytes(
        document, media_type="application/json", classification="workspace_snapshot"
    )
    return WorkspaceSnapshot(
        revision="a" * 40,
        source_content_sha256="b" * 64,
        workspace_content_sha256="c" * 64,
        baseline_revision="a" * 40,
        baseline_files_artifact=baseline_ref,
        terminal_files_artifact=terminal_ref,
        patch_artifact=patch_ref,
        canonical_artifact=canonical_ref,
        canonical_sha256=canonical_ref.sha256,
    )


def _contract(
    store: InMemoryArtifactStore, snapshot: WorkspaceSnapshot, script: str, **changes: object
) -> GraderContract:
    assert snapshot.baseline_files_artifact is not None
    native = store.put_bytes(
        script.encode(), media_type="text/x-python", classification="grader_native_tests"
    )
    hidden = store.put_bytes(
        b"assert True\n", media_type="text/x-python", classification="grader_hidden_tests"
    )
    dependencies = store.put_bytes(
        b"fixture-dependency==1\n", media_type="text/plain", classification="grader_dependencies"
    )
    values: dict[str, object] = {
        "grader_version": "fixture-grader/1",
        "grader_sha256": "d" * 64,
        "native_tests_artifact": native,
        "hidden_tests_artifact": hidden,
        "dependencies_artifact": dependencies,
        "scope_policy_sha256": "e" * 64,
        "tamper_policy_sha256": "f" * 64,
        "command": ("{python}", "{native_tests}", "{hidden_tests}", "{snapshot}"),
        "allowed_paths": ("app.py",),
        "forbidden_paths": ("secrets/**",),
        "expected_baseline_revision": "a" * 40,
        "expected_baseline_files_sha256": snapshot.baseline_files_artifact.sha256,
    }
    values.update(changes)
    return GraderContract(**values)  # type: ignore[arg-type]


def _passing_script() -> str:
    return (
        "import json, pathlib, sys\n"
        "hidden, snapshot = sys.argv[1:]\n"
        "assert pathlib.Path(hidden).read_bytes() == b'assert True\\n'\n"
        "assert (pathlib.Path(snapshot) / 'app.py').read_text() == 'answer = 2\\n'\n"
        "sys.stdout.write(json.dumps({'schema_version': '1.0.0', 'native_tests': True, "
        "'hidden_tests': True, 'continuous_score': 1.0, "  # noqa: E501
        "'objective_components': {'fraction': 1.0}}, "
        "sort_keys=True, separators=(',', ':')))\n"
    )


def _grade(
    store: InMemoryArtifactStore,
    snapshot: WorkspaceSnapshot,
    script: str | None = None,
    timeout_seconds: float = 5.0,
    **changes: object,
) -> GraderResult:
    grader = CredentialFreeExecutableGrader(store, timeout_seconds=timeout_seconds)
    return asyncio.run(
        grader.grade(snapshot, _contract(store, snapshot, script or _passing_script(), **changes))
    )


def _sandbox_diagnostic_bytes(store: InMemoryArtifactStore, result: GraderResult) -> list[bytes]:
    return [
        payload
        for reference in result.evidence_refs
        if b'"authority":"sandbox"' in (payload := store.open_verified(reference))
    ]


def _sandbox_diagnostics(
    store: InMemoryArtifactStore, result: GraderResult
) -> list[dict[str, object]]:
    return [json.loads(payload) for payload in _sandbox_diagnostic_bytes(store, result)]


def test_identical_frozen_snapshot_and_contract_replay_exactly() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)

    first = _grade(store, snapshot)
    second = _grade(store, snapshot)

    assert first.terminal is GraderTerminalKind.PASSED
    assert first.test_outcomes == second.test_outcomes
    assert first.continuous_score == second.continuous_score == 1.0
    assert compare_grading_replays(first, second).matches is True


def test_grading_reads_detached_bytes_not_a_later_mutable_workspace(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    mutable = tmp_path / "workspace"
    mutable.mkdir()
    (mutable / "app.py").write_text("answer = 999\n", encoding="utf-8")

    result = _grade(store, snapshot)

    assert result.terminal is GraderTerminalKind.PASSED


def test_partial_or_corrupt_snapshot_fails_closed() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    partial = WorkspaceSnapshot(
        snapshot.revision, snapshot.source_content_sha256, snapshot.workspace_content_sha256
    )

    grader = CredentialFreeExecutableGrader(store)
    contract = _contract(store, snapshot, _passing_script())
    assert asyncio.run(grader.grade(partial, contract)).terminal is GraderTerminalKind.BLOCKED
    assert snapshot.canonical_artifact is not None
    store._blobs[snapshot.canonical_artifact.sha256] = b"{}"
    assert _grade(store, snapshot).terminal is GraderTerminalKind.BLOCKED


def test_path_escape_and_scope_tampering_are_blocked() -> None:
    store = InMemoryArtifactStore()
    escaped = _snapshot(store, terminal={"../escape.py": b"bad"})

    assert _grade(store, escaped).terminal is GraderTerminalKind.BLOCKED
    out_of_scope = _snapshot(store, terminal={"README.md": b"changed"})
    assert _grade(store, out_of_scope).terminal is GraderTerminalKind.BLOCKED


@pytest.mark.parametrize(
    "path",
    (
        "Secrets/leak.txt",
        "Secrets\\leak.txt",
        "secrets/leak.txt ",
        "secrets/leak.txt.",
        "secrets/../leak.txt",
    ),
)
def test_scope_policy_normalizes_case_and_separators_and_rejects_aliases(path: str) -> None:
    store = InMemoryArtifactStore()
    baseline = {"app.py": b"answer = 2\n", "secrets/leak.txt": b"old"}
    terminal = {"app.py": b"answer = 2\n", path: b"new"}
    snapshot = _snapshot(store, baseline=baseline, terminal=terminal)

    result = _grade(
        store,
        snapshot,
        allowed_paths=("app.py", "secrets/**"),
        forbidden_paths=("secrets/**",),
    )

    assert result.terminal is GraderTerminalKind.BLOCKED


def test_snapshot_rejects_unicode_path_normalization_aliases() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(
        store,
        terminal={"caf\u00e9.py": b"one", "cafe\u0301.py": b"two"},
    )

    assert _grade(store, snapshot).terminal is GraderTerminalKind.BLOCKED


def test_frozen_baseline_and_contract_hash_mismatch_are_blocked() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)

    result = _grade(store, snapshot, expected_baseline_revision="b" * 40)

    assert result.terminal is GraderTerminalKind.BLOCKED
    assert result.binary_passed is None


def test_grader_receives_no_provider_credentials_or_assignment() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    script = (
        "import json, os, sys\n"
        "blocked = {'OPENAI_API_KEY', 'GITHUB_TOKEN', 'GH_TOKEN', 'COPILOT_AUTH_TOKEN'}\n"
        "assert not blocked.intersection(os.environ)\n"
        "assert not any('assignment' in name.lower() for name in os.environ)\n"
        "sys.stdout.write(json.dumps({'schema_version': '1.0.0', 'native_tests': True, "
        "'hidden_tests': True, 'continuous_score': 1.0, "  # noqa: E501
        "'objective_components': {'credential_free': 1.0}}, "
        "sort_keys=True, separators=(',', ':')))\n"
    )

    assert _grade(store, snapshot, script).terminal is GraderTerminalKind.PASSED
    assert "assignment" not in inspect.signature(CredentialFreeExecutableGrader.grade).parameters


def test_network_policy_is_enforced_inside_the_grader_process() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    script = "import socket\nsocket.create_connection(('example.invalid', 443))\n"

    result = _grade(store, snapshot, script)

    assert result.terminal is GraderTerminalKind.UNAVAILABLE


@pytest.mark.parametrize(
    ("script", "terminal"),
    (
        (
            "import sys\nsys.stdout.write('diagnostic\\n"
            '{"continuous_score":1.0,"hidden_tests":true,"native_tests":true,'
            '"objective_components":{"fraction":1.0},"schema_version":"1.0.0"}\')\n',
            GraderTerminalKind.UNAVAILABLE,
        ),
        (
            'import sys\npayload=\'{"continuous_score":1.0,"hidden_tests":true,'
            '"native_tests":true,"objective_components":{"fraction":1.0},'
            '"schema_version":"1.0.0"}\'\nsys.stdout.write(payload + payload)\n',
            GraderTerminalKind.UNAVAILABLE,
        ),
        ("import sys\nsys.stdout.write('{not-json}')\n", GraderTerminalKind.UNAVAILABLE),
        (
            'import sys\nsys.stdout.write(\'{"schema_version":"1.0.0"}\')\n',
            GraderTerminalKind.UNAVAILABLE,
        ),
        (
            'import sys\nsys.stdout.write(\'{"continuous_score":1.0,"hidden_tests":true,'
            '"native_tests":"true","objective_components":{"fraction":1.0},'
            '"schema_version":"1.0.0"}\')\n',
            GraderTerminalKind.UNAVAILABLE,
        ),
        (
            'import sys\nsys.stdout.write(\'{"continuous_score":0.0,"hidden_tests":true,'
            '"native_tests":false,"objective_components":{"fraction":0.0},'
            '"schema_version":"1.0.0"}\')\n',
            GraderTerminalKind.FAILED,
        ),
    ),
)
def test_grader_output_parser_rejects_ambiguous_or_invalid_documents(
    script: str, terminal: GraderTerminalKind
) -> None:
    store = InMemoryArtifactStore()

    result = _grade(store, _snapshot(store), script)

    assert result.terminal is terminal
    assert result.binary_passed is not True
    if terminal is GraderTerminalKind.UNAVAILABLE:
        assert any(
            diagnostic["phase"] == "output_parse" and diagnostic["code"] == "malformed_output"
            for diagnostic in _sandbox_diagnostics(store, result)
        )


@pytest.mark.parametrize("padding", ("", " ", "\t", "\n", " \t\r\n"))
def test_grader_output_parser_accepts_exact_json_with_only_outer_whitespace(padding: str) -> None:
    store = InMemoryArtifactStore()
    script = (
        "import json\n"
        "print(json.dumps({'schema_version':'1.0.0','native_tests':True,'hidden_tests':True,"
        "'continuous_score':1.0,'objective_components':{'fraction':1.0}},"
        "sort_keys=True,separators=(',',':')), end='')\n"
    )
    if padding:
        script = f"import sys\nsys.stdout.write({padding!r})\n" + script
        script += f"print({padding!r}, end='')\n"

    assert _grade(store, _snapshot(store), script).terminal is GraderTerminalKind.PASSED


def test_network_namespace_blocks_listener_and_host_escapes(tmp_path: Path) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(1.0)
    port = listener.getsockname()[1]
    accepted: list[object] = []

    def accept_once() -> None:
        try:
            accepted.append(listener.accept())
        except TimeoutError:
            return

    thread = threading.Thread(target=accept_once)
    thread.start()
    host_sentinel = tmp_path / "host-sentinel.txt"
    host_sentinel.write_text("must not be readable", encoding="utf-8")
    script = (
        "import json, os, pathlib, shlex, shutil, socket, ssl, subprocess, sys\n"
        f"port={port}\n"
        f"host_sentinel={str(host_sentinel)!r}\n"
        "captured_system=os.system\n"
        "captured_popen=subprocess.Popen\n"
        "def denied(operation):\n"
        "    try:\n"
        "        operation()\n"
        "    except OSError:\n"
        "        return True\n"
        "    return False\n"
        "direct=denied(lambda: socket.create_connection(('127.0.0.1', port), timeout=0.2))\n"
        "probe=socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "connect_ex=probe.connect_ex(('127.0.0.1', port)) != 0\n"
        "probe.close()\n"
        "child=subprocess.run([sys.executable, '-c', "
        "'import socket,sys; sys.exit(0 if socket.socket().connect_ex((\"127.0.0.1\", '"
        "+str(port)+')) else 1)']).returncode == 0\n"
        "spawn_code='import socket,sys;sys.exit(0 if socket.socket().connect_ex("
        "(\"127.0.0.1\",'+str(port)+')) else 1)'\n"
        "popen=captured_popen((sys.executable,'-c',spawn_code)).wait() == 0\n"
        "system=captured_system(shlex.join((sys.executable,'-c',spawn_code))) == 0\n"
        "native_ssl=denied(lambda: ssl.create_default_context().wrap_socket("
        "socket.socket(),server_hostname='localhost').connect(('127.0.0.1',port)))\n"
        "ipv6=denied(lambda: socket.create_connection(('::1', port), timeout=0.2)) "
        "if socket.has_ipv6 else True\n"
        "dns=denied(lambda: socket.getaddrinfo('example.invalid', port))\n"
        "sudo=not shutil.which('sudo') or subprocess.run(['sudo','-n','true'], "
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode != 0\n"
        "nsenter=not shutil.which('nsenter') or subprocess.run(['nsenter','--net=/proc/1/ns/net',"
        "'true'], "
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode != 0\n"
        "docker=not os.path.exists('/var/run/docker.sock')\n"
        "host_file=not os.path.exists(host_sentinel)\n"
        "input_readable=pathlib.Path(sys.argv[2], 'app.py').read_text() == 'answer = 2\\n'\n"
        "input_immutable=not os.access(sys.argv[2], os.W_OK)\n"
        "unprivileged=os.geteuid() == 65534\n"
        "checks={'direct':direct,'connect_ex':connect_ex,'child':child,'popen':popen,'system':system,"
        "'native_ssl':native_ssl,'ipv6':ipv6,'dns':dns,"
        "'sudo':sudo,'nsenter':nsenter,'host_file':host_file,'input_readable':input_readable,"
        "'input_immutable':input_immutable,'unprivileged':unprivileged,'docker':docker}\n"
        "sys.stdout.write(json.dumps({'schema_version':'1.0.0','native_tests':True,"
        "'hidden_tests':True,'continuous_score':1.0,'objective_components':"
        "{name:float(value) for name,value in checks.items()}},"
        "sort_keys=True,separators=(',',':')))\n"
    )
    try:
        store = InMemoryArtifactStore()
        result = _grade(store, _snapshot(store), script, 5.0)
    finally:
        thread.join()
        listener.close()

    assert _sandbox_diagnostics(store, result) == []
    assert result.terminal is GraderTerminalKind.PASSED
    assert result.objective_components == {
        "child": 1.0,
        "connect_ex": 1.0,
        "direct": 1.0,
        "docker": 1.0,
        "dns": 1.0,
        "host_file": 1.0,
        "input_immutable": 1.0,
        "input_readable": 1.0,
        "ipv6": 1.0,
        "native_ssl": 1.0,
        "nsenter": 1.0,
        "popen": 1.0,
        "sudo": 1.0,
        "system": 1.0,
        "unprivileged": 1.0,
    }
    assert accepted == []


def test_docker_profile_is_pinned_and_restricted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        executable.shutil, "which", lambda tool: "/usr/bin/docker" if tool == "docker" else None
    )

    command = executable._docker_sandbox_create_command(
        (str(Path(sys.executable).resolve()), str(tmp_path / "native_tests.py")),
        cwd=tmp_path / "snapshot",
        environment={"HOME": "/tmp", "PATH": "/usr/local/bin:/usr/bin:/bin"},
        container_name="memrelay-grader-fixture",
    )

    assert executable._DOCKER_PYTHON_IMAGE in command
    assert "@sha256:" in executable._DOCKER_PYTHON_IMAGE
    assert command[command.index("--network") : command.index("--network") + 2] == (
        "--network",
        "none",
    )
    assert command[command.index("--user") : command.index("--user") + 2] == (
        "--user",
        "65534:65534",
    )
    assert command[command.index("--cap-drop") : command.index("--cap-drop") + 2] == (
        "--cap-drop",
        "ALL",
    )
    assert command[command.index("--security-opt") : command.index("--security-opt") + 2] == (
        "--security-opt",
        "no-new-privileges:true",
    )
    assert "--read-only" in command
    assert command[command.index("--pids-limit") : command.index("--pids-limit") + 2] == (
        "--pids-limit",
        "64",
    )
    assert "/var/run/docker.sock" not in command
    assert str(Path.home()) not in command
    assert str(Path("/")) not in command
    assert command.count("--mount") == 1
    assert command.count("--tmpfs") == 2


def test_docker_timeout_removes_exact_created_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container_id = "a" * 64
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(executable, "_require_pinned_docker_image", lambda: None)
    monkeypatch.setattr(
        executable.shutil, "which", lambda tool: "/usr/bin/docker" if tool == "docker" else None
    )

    def run(command: tuple[str, ...], **_: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 0, stdout=f"{container_id}\n".encode())
        if command[1] == "start":
            raise subprocess.TimeoutExpired(command, 1, output=b"partial", stderr=b"late")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(executable.subprocess, "run", run)

    completed = executable._run_docker_sandbox(
        (str(Path(sys.executable).resolve()), "-c", "pass"),
        cwd=tmp_path,
        environment={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        timeout_seconds=1,
    )

    assert completed.returncode == 124
    assert ("/usr/bin/docker", "kill", container_id) in calls
    assert ("/usr/bin/docker", "rm", "--force", container_id) in calls


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker sandbox unavailable")
def test_docker_fallback_blocks_live_host_escapes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(1.0)
    port = listener.getsockname()[1]
    host_sentinel = tmp_path / "host-sentinel.txt"
    host_sentinel.write_text("must not be readable", encoding="utf-8")
    script = (
        "import json, os, pathlib, shutil, socket, subprocess, sys\n"
        f"port={port}\n"
        f"host_sentinel={str(host_sentinel)!r}\n"
        "def denied(operation):\n"
        "    try:\n"
        "        operation()\n"
        "    except OSError:\n"
        "        return True\n"
        "    return False\n"
        "direct=denied(lambda: socket.create_connection(('127.0.0.1',port),timeout=0.2))\n"
        "probe=socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
        "connect_ex=probe.connect_ex(('127.0.0.1',port)) != 0\n"
        "probe.close()\n"
        "child=subprocess.run([sys.executable,'-c',"
        f"'import socket;raise SystemExit(socket.socket().connect_ex((\"127.0.0.1\",{port})) == 0)'"
        "])"
        ".returncode == 0\n"
        "ipv6=denied(lambda:socket.create_connection(('::1',port),timeout=0.2)) "
        "if socket.has_ipv6 else True\n"
        "dns=denied(lambda:socket.getaddrinfo('example.invalid',port))\n"
        "docker=not shutil.which('docker') and not os.path.exists('/var/run/docker.sock')\n"
        "host_file=not os.path.exists(host_sentinel)\n"
        "input=pathlib.Path(sys.argv[2],'app.py')\n"
        "input_readable=input.read_text() == 'answer = 2\\n'\n"
        "input_immutable=not os.access(input,os.W_OK)\n"
        "checks={'direct':direct,'connect_ex':connect_ex,'child':child,'ipv6':ipv6,'dns':dns,"
        "'docker':docker,'host_file':host_file,"
        "'input_readable':input_readable,'input_immutable':input_immutable,"
        "'unprivileged':os.geteuid()==65534}\n"
        "sys.stdout.write(json.dumps({'schema_version':'1.0.0','native_tests':True,"
        "'hidden_tests':True,'continuous_score':1.0,'objective_components':"
        "{name:float(value) for name,value in checks.items()}},"
        "sort_keys=True,separators=(',',':')))\n"
    )
    monkeypatch.setattr(executable, "_require_network_sandbox", lambda *_: "docker")
    store = InMemoryArtifactStore()
    try:
        result = _grade(store, _snapshot(store), script, 10.0)
    finally:
        listener.close()

    assert _sandbox_diagnostics(store, result) == []
    assert result.terminal is GraderTerminalKind.PASSED
    assert result.objective_components == {
        "child": 1.0,
        "connect_ex": 1.0,
        "direct": 1.0,
        "docker": 1.0,
        "dns": 1.0,
        "host_file": 1.0,
        "input_immutable": 1.0,
        "input_readable": 1.0,
        "ipv6": 1.0,
        "unprivileged": 1.0,
    }
    docker = shutil.which("docker")
    assert docker is not None
    containers = subprocess.run(
        (docker, "ps", "--all", "--format", "{{.Names}}"),
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    assert not any(name.startswith("memrelay-grader-") for name in containers)


def test_secret_output_blocks_without_persisting_secret_bytes() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    secret = "sk-" + "z" * 24
    result = _grade(store, snapshot, "print('sk-' + 'z' * 24)\n")

    assert result.terminal is GraderTerminalKind.BLOCKED
    assert result.raw_output_artifact is None
    assert all(secret.encode() not in store.open_verified(ref) for ref in result.evidence_refs)


def test_timeout_and_crash_preserve_partial_raw_evidence() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    timeout = _grade(
        store,
        snapshot,
        "import time\nprint('started', flush=True)\ntime.sleep(10)\n",
        timeout_seconds=2.0,
    )
    crash = _grade(
        store,
        snapshot,
        "print('before crash', flush=True)\nraise RuntimeError('fixture')\n",
        timeout_seconds=5.0,
    )

    assert timeout.terminal is GraderTerminalKind.UNAVAILABLE
    assert crash.terminal is GraderTerminalKind.UNAVAILABLE


@pytest.mark.parametrize(
    ("failure", "completed", "expected"),
    (
        (
            "selection",
            None,
            {
                "schema_version": "1.0.0",
                "authority": "sandbox",
                "phase": "selection_probe",
                "code": "authority_unavailable",
            },
        ),
        (
            "launch",
            None,
            {
                "schema_version": "1.0.0",
                "authority": "sandbox",
                "phase": "profile_launch",
                "code": "profile_launch_failed",
            },
        ),
        (
            "timeout",
            subprocess.CompletedProcess(
                ("fixture",),
                124,
                stdout=b"candidate output must never enter the diagnostic",
                stderr=b"host path /private/fixture must never enter the diagnostic",
            ),
            {
                "schema_version": "1.0.0",
                "authority": "sandbox",
                "phase": "candidate_runtime",
                "code": "timeout",
            },
        ),
        (
            "crash",
            subprocess.CompletedProcess(
                ("fixture",),
                1,
                stdout=b"candidate output must never enter the diagnostic",
                stderr=b"host path /private/fixture must never enter the diagnostic",
            ),
            {
                "schema_version": "1.0.0",
                "authority": "sandbox",
                "phase": "candidate_runtime",
                "code": "crash",
            },
        ),
        (
            "parse",
            subprocess.CompletedProcess(
                ("fixture",),
                0,
                stdout=b"candidate output must never enter the diagnostic",
                stderr=b"host path /private/fixture must never enter the diagnostic",
            ),
            {
                "schema_version": "1.0.0",
                "authority": "sandbox",
                "phase": "output_parse",
                "code": "malformed_output",
            },
        ),
    ),
)
def test_sandbox_diagnostic_matrix_is_exact_and_value_free(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    completed: subprocess.CompletedProcess[bytes] | None,
    expected: dict[str, str],
) -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    grader = CredentialFreeExecutableGrader(store)
    contract = _contract(store, snapshot, _passing_script())
    if failure == "selection":
        monkeypatch.setattr(
            executable,
            "_require_network_sandbox",
            lambda *_: (_ for _ in ()).throw(NetworkSandboxUnavailableError()),
        )
    else:
        monkeypatch.setattr(executable, "_require_network_sandbox", lambda *_: "bubblewrap")
        if failure == "launch":
            monkeypatch.setattr(
                executable,
                "_run_python_in_network_sandbox",
                lambda *_, **__: (_ for _ in ()).throw(NetworkSandboxUnavailableError()),
            )
        else:
            assert completed is not None
            monkeypatch.setattr(
                executable,
                "_run_python_in_network_sandbox",
                lambda *_, **__: completed,
            )

    result = grader._grade(snapshot, contract)

    assert result.terminal is GraderTerminalKind.UNAVAILABLE
    assert _sandbox_diagnostics(store, result) == [expected]
    diagnostic = _sandbox_diagnostic_bytes(store, result)
    assert diagnostic == [canonical_bytes(expected)]
    assert b"candidate output" not in diagnostic[0]
    assert b"/private/fixture" not in diagnostic[0]
    assert b"terminal" not in diagnostic[0]
    result_document = json.loads(store.open_verified(result.result_artifact))
    assert result_document["snapshot_sha256"] == snapshot.canonical_sha256
    assert result_document["contract_sha256"] == canonical_digest(contract.to_record())
    assert {reference.sha256 for reference in grader._input_evidence(snapshot, contract)}.issubset(
        result_document["evidence_sha256"]
    )


def test_replay_disagreement_and_flaky_favorable_run_cannot_be_substituted() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    passed = _grade(store, snapshot)
    failed = _grade(
        store,
        snapshot,
        "import json, sys\n"
        "sys.stdout.write(json.dumps({'schema_version': '1.0.0', 'native_tests': False, "
        "'hidden_tests': True, 'continuous_score': 0.0, "  # noqa: E501
        "'objective_components': {'fraction': 0.0}}, "
        "sort_keys=True, separators=(',', ':')))\n",
    )

    assert compare_grading_replays(passed, failed).matches is False
    with pytest.raises(GraderReplayMismatchError) as raised:
        require_matching_replays(passed, failed)
    assert raised.value.code == "grader_replay_mismatch"
    classification = classify_candidate_flakiness((False, True, False), (False, True, False))
    assert classification.is_preregistered_flaky_signature is True
    assert classification.aggregate_passed is False


def test_intake_requires_five_fresh_baseline_and_gold_patch_passes() -> None:
    require_intake_stability((True,) * 5, (True,) * 5)
    with pytest.raises(GraderContractError, match="grader baseline instability"):
        require_intake_stability((True, True, False, True, True), (True,) * 5)
    with pytest.raises(GraderContractError, match="grader gold patch instability"):
        require_intake_stability((True,) * 5, (True, True, False, True, True))


def test_regrade_reuses_the_same_snapshot_and_contract_with_a_bounded_count() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    contract = _contract(store, snapshot, "raise RuntimeError('unavailable')\n", maximum_regrades=1)
    unavailable = asyncio.run(CredentialFreeExecutableGrader(store).grade(snapshot, contract))

    class UnavailableGrader:
        def __init__(self) -> None:
            self.calls: list[tuple[object, GraderContract]] = []

        async def grade(
            self, received_snapshot: object, received_contract: GraderContract
        ) -> GraderResult:
            self.calls.append((received_snapshot, received_contract))
            return unavailable

    grader = UnavailableGrader()
    results = asyncio.run(grade_with_bounded_regrades(grader, snapshot, contract))

    assert results == (unavailable, unavailable)
    assert grader.calls == [(snapshot, contract), (snapshot, contract)]


def test_concurrent_attempts_use_distinct_detached_snapshot_bytes() -> None:
    store = InMemoryArtifactStore()
    first = _snapshot(store, terminal={"app.py": b"answer = 2\n"})
    second = _snapshot(store, terminal={"app.py": b"answer = 3\n"})
    script = (
        "import json, pathlib, sys\n"
        "sys.stdout.write(json.dumps({'schema_version': '1.0.0', "
        "'native_tests': pathlib.Path(sys.argv[2], 'app.py').exists(), 'hidden_tests': True, "
        "'continuous_score': 1.0, 'objective_components': {'isolated': 1.0}}, "
        "sort_keys=True, separators=(',', ':')))\n"
    )
    grader = CredentialFreeExecutableGrader(store)
    first_contract = _contract(store, first, script)
    second_contract = _contract(store, second, script)

    async def grade_both() -> tuple[GraderResult, GraderResult]:
        first_result, second_result = await asyncio.gather(
            grader.grade(first, first_contract), grader.grade(second, second_contract)
        )
        return first_result, second_result

    results = asyncio.run(grade_both())

    assert all(result.terminal is GraderTerminalKind.PASSED for result in results)
    assert results[0].snapshot_sha256 != results[1].snapshot_sha256


def test_fake_artifacts_remain_explicitly_unpaid() -> None:
    store = InMemoryArtifactStore()

    require_unpaid_conformance_ports(store, CredentialFreeExecutableGrader(store))
    assert store.eligible_for_paid_or_study is False
