"""Credential-free executable grading over detached immutable workspace bytes."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import functools
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from memrelay_eval.adapters.process.environment import ProcessRole, build_process_environment
from memrelay_eval.adapters.workspace.base import WorkspaceSnapshot
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.entities import ArtifactRef, GraderContract, GraderResult
from memrelay_eval.domain.errors import (
    ArtifactIntegrityError,
    MalformedGraderOutputError,
    NetworkSandboxUnavailableError,
    SnapshotIntegrityError,
)
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.domain.states import GraderTerminalKind
from memrelay_eval.evidence.secret_scan import scan_secret_boundaries


class CredentialFreeExecutableGrader:
    """Runs only a hash-pinned Python test command in a minimal child environment."""

    provenance = "unpaid_conformance"
    eligible_for_paid_or_study = False

    def __init__(self, artifact_store: ArtifactStorePort, *, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("grader timeout must be positive")
        self._artifact_store = artifact_store
        self._timeout_seconds = timeout_seconds

    async def grade(self, snapshot: WorkspaceSnapshot, contract: GraderContract) -> GraderResult:
        return await asyncio.to_thread(self._grade, snapshot, contract)

    def _grade(self, snapshot: WorkspaceSnapshot, contract: GraderContract) -> GraderResult:
        contract_sha256 = canonical_digest(contract.to_record())
        try:
            snapshot_document = self._verify_snapshot(snapshot)
        except (ArtifactIntegrityError, SnapshotIntegrityError, ValueError):
            return self._blocked_result(snapshot, contract_sha256, "snapshot_integrity")
        try:
            changed_paths = _changed_paths(
                self._load_files(snapshot.baseline_files_artifact),
                self._load_files(snapshot.terminal_files_artifact),
            )
        except (ArtifactIntegrityError, SnapshotIntegrityError, ValueError, json.JSONDecodeError):
            return self._blocked_result(snapshot, contract_sha256, "snapshot_file_inventory")
        if not self._contract_matches_snapshot(contract, snapshot, snapshot_document):
            return self._blocked_result(snapshot, contract_sha256, "contract_or_tamper_mismatch")
        if not _scope_is_allowed(changed_paths, contract):
            return self._blocked_result(snapshot, contract_sha256, "patch_scope")

        try:
            return self._run(snapshot, contract, contract_sha256)
        except (ArtifactIntegrityError, OSError, ValueError, json.JSONDecodeError):
            return self._unavailable_result(snapshot, contract_sha256, "grader_setup_failure")

    def _run(
        self, snapshot: WorkspaceSnapshot, contract: GraderContract, contract_sha256: str
    ) -> GraderResult:
        assert snapshot.terminal_files_artifact is not None
        input_evidence = self._input_evidence(snapshot, contract)
        with tempfile.TemporaryDirectory(prefix="memrelay-grader-") as temporary:
            root = Path(temporary)
            snapshot_root = root / "snapshot"
            snapshot_root.mkdir()
            _materialize_files(self._load_files(snapshot.terminal_files_artifact), snapshot_root)
            native_tests = root / "native_tests.py"
            hidden_tests = root / "hidden_tests.py"
            dependencies = root / "dependencies.lock"
            native_bytes = self._artifact_store.open_verified(contract.native_tests_artifact)
            hidden_bytes = self._artifact_store.open_verified(contract.hidden_tests_artifact)
            dependency_bytes = self._artifact_store.open_verified(contract.dependencies_artifact)
            contract_findings = scan_secret_boundaries(
                {
                    "native_tests": native_bytes,
                    "hidden_tests": hidden_bytes,
                    "dependencies": dependency_bytes,
                }
            )
            if contract_findings:
                finding_artifact = self._artifact_store.put_bytes(
                    canonical_bytes(
                        {"findings": [finding.to_dict() for finding in contract_findings]}
                    ),
                    media_type="application/json",
                    classification="secret_boundary_finding",
                )
                return self._result(
                    snapshot,
                    contract_sha256,
                    GraderTerminalKind.BLOCKED,
                    None,
                    {},
                    None,
                    {},
                    None,
                    (*input_evidence, finding_artifact),
                )
            native_tests.write_bytes(native_bytes)
            hidden_tests.write_bytes(hidden_bytes)
            dependencies.write_bytes(dependency_bytes)
            command = _resolve_command(
                contract.command,
                snapshot_root=snapshot_root,
                native_tests=native_tests,
                hidden_tests=hidden_tests,
                dependencies=dependencies,
            )
            environment = _minimal_grader_environment(root)
            try:
                _require_network_sandbox()
                completed = _run_python_in_network_sandbox(
                    command,
                    cwd=snapshot_root,
                    environment=environment,
                    timeout_seconds=self._timeout_seconds,
                )
            except NetworkSandboxUnavailableError:
                return self._unavailable_result(
                    snapshot,
                    contract_sha256,
                    "network_sandbox_unavailable",
                    input_evidence,
                )
            raw_output = completed.stdout + b"\n--- stderr ---\n" + completed.stderr
            findings = scan_secret_boundaries({"grader_output": raw_output})
            if findings:
                finding_artifact = self._artifact_store.put_bytes(
                    canonical_bytes({"findings": [finding.to_dict() for finding in findings]}),
                    media_type="application/json",
                    classification="secret_boundary_finding",
                )
                return self._result(
                    snapshot,
                    contract_sha256,
                    GraderTerminalKind.BLOCKED,
                    None,
                    {},
                    None,
                    {},
                    None,
                    (*input_evidence, finding_artifact),
                )
            raw_artifact = self._artifact_store.put_bytes(
                raw_output, media_type="text/plain", classification="grader_raw_output"
            )
            return _result_from_process(
                self, snapshot, contract_sha256, completed, raw_artifact, input_evidence
            )

    def _verify_snapshot(self, snapshot: WorkspaceSnapshot) -> Mapping[str, object]:
        required = (
            snapshot.baseline_revision,
            snapshot.baseline_files_artifact,
            snapshot.terminal_files_artifact,
            snapshot.patch_artifact,
            snapshot.canonical_artifact,
            snapshot.canonical_sha256,
        )
        if any(value is None for value in required):
            raise SnapshotIntegrityError("workspace snapshot is not detached")
        assert snapshot.canonical_artifact is not None
        assert snapshot.canonical_sha256 is not None
        if snapshot.canonical_artifact.sha256 != snapshot.canonical_sha256:
            raise SnapshotIntegrityError("workspace snapshot canonical identity mismatch")
        raw = self._artifact_store.open_verified(snapshot.canonical_artifact)
        document = json.loads(raw)
        if not isinstance(document, dict) or canonical_bytes(document) != raw:
            raise SnapshotIntegrityError("workspace snapshot canonical artifact is malformed")
        expected = {
            "baseline_files_sha256": snapshot.baseline_files_artifact.sha256,
            "terminal_files_sha256": snapshot.terminal_files_artifact.sha256,
            "patch_sha256": snapshot.patch_artifact.sha256,
            "baseline_revision": snapshot.baseline_revision,
            "terminal_revision": snapshot.revision,
        }
        if any(document.get(key) != value for key, value in expected.items()):
            raise SnapshotIntegrityError("workspace snapshot artifacts disagree")
        for artifact in (
            snapshot.baseline_files_artifact,
            snapshot.terminal_files_artifact,
            snapshot.patch_artifact,
        ):
            self._artifact_store.open_verified(artifact)
        return document

    def _contract_matches_snapshot(
        self, contract: GraderContract, snapshot: WorkspaceSnapshot, document: Mapping[str, object]
    ) -> bool:
        return (
            contract.expected_baseline_revision == snapshot.baseline_revision
            and contract.expected_baseline_files_sha256 == document.get("baseline_files_sha256")
            and contract.network_policy == "deny"
        )

    def _load_files(self, artifact: ArtifactRef | None) -> Mapping[str, tuple[int, bytes]]:
        if artifact is None:
            raise SnapshotIntegrityError("workspace file inventory is missing")
        raw = self._artifact_store.open_verified(artifact)
        document = json.loads(raw)
        if not isinstance(document, dict) or canonical_bytes(document) != raw:
            raise SnapshotIntegrityError("workspace file inventory is noncanonical")
        files = document.get("files")
        if document.get("schema_version") != "1.0.0" or not isinstance(files, list):
            raise SnapshotIntegrityError("workspace file inventory has an invalid schema")
        result: dict[str, tuple[int, bytes]] = {}
        for item in files:
            if not isinstance(item, dict):
                raise SnapshotIntegrityError("workspace file inventory contains an invalid entry")
            path = item.get("path")
            mode = item.get("mode")
            encoded = item.get("content_base64")
            digest = item.get("sha256")
            if (
                not isinstance(path, str)
                or not isinstance(mode, int)
                or not isinstance(encoded, str)
                or not isinstance(digest, str)
                or not _safe_relative_path(path)
                or path in result
            ):
                raise SnapshotIntegrityError("workspace file inventory path is invalid")
            data = base64.b64decode(encoded, validate=True)
            if sha256(data).hexdigest() != digest:
                raise SnapshotIntegrityError("workspace file inventory content hash mismatch")
            result[path] = (mode, data)
        return result

    def _blocked_result(
        self, snapshot: WorkspaceSnapshot, contract_sha256: str, reason: str
    ) -> GraderResult:
        marker = self._artifact_store.put_bytes(
            canonical_bytes({"terminal": "blocked", "reason": reason}),
            media_type="application/json",
            classification="grader_result",
        )
        return self._result(
            snapshot,
            contract_sha256,
            GraderTerminalKind.BLOCKED,
            None,
            {},
            None,
            {},
            None,
            (marker,),
        )

    def _unavailable_result(
        self,
        snapshot: WorkspaceSnapshot,
        contract_sha256: str,
        reason: str,
        evidence: tuple[ArtifactRef, ...] = (),
    ) -> GraderResult:
        marker = self._artifact_store.put_bytes(
            canonical_bytes({"terminal": "unavailable", "reason": reason}),
            media_type="application/json",
            classification="grader_result",
        )
        return self._result(
            snapshot,
            contract_sha256,
            GraderTerminalKind.UNAVAILABLE,
            None,
            {},
            None,
            {},
            None,
            (*evidence, marker),
        )

    def _result(
        self,
        snapshot: WorkspaceSnapshot,
        contract_sha256: str,
        terminal: GraderTerminalKind,
        binary_passed: bool | None,
        tests: Mapping[str, bool],
        continuous_score: float | None,
        components: Mapping[str, float],
        raw_output: ArtifactRef | None,
        evidence: tuple[ArtifactRef, ...],
    ) -> GraderResult:
        snapshot_sha256 = snapshot.canonical_sha256 or "0" * 64
        result_artifact = self._artifact_store.put_bytes(
            canonical_bytes(
                {
                    "schema_version": "1.0.0",
                    "snapshot_sha256": snapshot_sha256,
                    "contract_sha256": contract_sha256,
                    "terminal": terminal.value,
                    "binary_passed": binary_passed,
                    "tests": dict(tests),
                    "continuous_score": continuous_score,
                    "objective_components": dict(components),
                    "raw_output_sha256": raw_output.sha256 if raw_output else None,
                    "evidence_sha256": [item.sha256 for item in evidence],
                }
            ),
            media_type="application/json",
            classification="grader_result",
        )
        return GraderResult(
            snapshot_sha256,
            contract_sha256,
            terminal,
            binary_passed,
            tests,
            continuous_score,
            components,
            raw_output,
            result_artifact,
            evidence,
        )

    @staticmethod
    def _input_evidence(
        snapshot: WorkspaceSnapshot, contract: GraderContract
    ) -> tuple[ArtifactRef, ...]:
        references = (
            snapshot.baseline_files_artifact,
            snapshot.terminal_files_artifact,
            snapshot.patch_artifact,
            snapshot.canonical_artifact,
            contract.native_tests_artifact,
            contract.hidden_tests_artifact,
            contract.dependencies_artifact,
        )
        return tuple(reference for reference in references if reference is not None)


def _result_from_process(
    grader: CredentialFreeExecutableGrader,
    snapshot: WorkspaceSnapshot,
    contract_sha256: str,
    completed: subprocess.CompletedProcess[bytes],
    raw_output: ArtifactRef,
    input_evidence: tuple[ArtifactRef, ...],
) -> GraderResult:
    try:
        payload = _parse_grader_result(completed.stdout)
    except MalformedGraderOutputError:
        marker = grader._artifact_store.put_bytes(
            canonical_bytes({"terminal": "unavailable", "reason": "grader_malformed_output"}),
            media_type="application/json",
            classification="grader_malformed_output",
        )
        return grader._result(
            snapshot,
            contract_sha256,
            GraderTerminalKind.UNAVAILABLE,
            None,
            {},
            None,
            {},
            raw_output,
            (*input_evidence, raw_output, marker),
        )
    tests = {
        "native": payload["native_tests"],
        "hidden": payload["hidden_tests"],
    }
    terminal = (
        GraderTerminalKind.PASSED
        if completed.returncode == 0 and all(tests.values())
        else GraderTerminalKind.FAILED
    )
    return grader._result(
        snapshot,
        contract_sha256,
        terminal,
        terminal is GraderTerminalKind.PASSED,
        tests,
        payload["continuous_score"],
        payload["objective_components"],
        raw_output,
        (*input_evidence, raw_output),
    )


def _parse_grader_result(data: bytes) -> dict[str, object]:
    """Accept one exact canonical frozen result and reject all output framing ambiguity."""
    try:
        text = data.decode("utf-8")
        start = len(text) - len(text.lstrip(" \t\r\n"))
        value, end = json.JSONDecoder(object_pairs_hook=_reject_duplicate_json_keys).raw_decode(
            text, start
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MalformedGraderOutputError() from error
    if text[end:].strip(" \t\r\n") or not isinstance(value, dict):
        raise MalformedGraderOutputError()
    if set(value) != {
        "schema_version",
        "native_tests",
        "hidden_tests",
        "continuous_score",
        "objective_components",
    }:
        raise MalformedGraderOutputError()
    if value["schema_version"] != "1.0.0":
        raise MalformedGraderOutputError()
    if not isinstance(value["native_tests"], bool) or not isinstance(value["hidden_tests"], bool):
        raise MalformedGraderOutputError()
    score = value["continuous_score"]
    if not isinstance(score, float) or not math.isfinite(score):
        raise MalformedGraderOutputError()
    components = value["objective_components"]
    if not isinstance(components, dict) or not components:
        raise MalformedGraderOutputError()
    if any(
        not isinstance(name, str)
        or not name
        or not isinstance(component, float)
        or not math.isfinite(component)
        for name, component in components.items()
    ):
        raise MalformedGraderOutputError()
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise json.JSONDecodeError("duplicate object key", "", 0)
        result[key] = value
    return result


def _network_sandbox_command(
    command: tuple[str, ...], *, cwd: Path | None = None
) -> tuple[str, ...]:
    """Use the preflight-proven OS sandbox authority for this Linux host."""
    kind = _network_sandbox_kind()
    if kind in {"bubblewrap", "sudo_bubblewrap"}:
        assert cwd is not None
        bubblewrap = shutil.which("bwrap")
        if bubblewrap is None:
            raise NetworkSandboxUnavailableError()
        prefix = () if kind == "bubblewrap" else (shutil.which("sudo"), "-n")
        user_namespace = ("--unshare-user",) if kind == "bubblewrap" else ()
        if any(value is None for value in prefix):
            raise NetworkSandboxUnavailableError()
        sandboxed_command = _remap_sandbox_command(command, cwd)
        return (
            *prefix,
            bubblewrap,
            "--die-with-parent",
            "--new-session",
            *user_namespace,
            "--unshare-net",
            "--unshare-pid",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/runtime",
            "--dir",
            "/inputs",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--ro-bind",
            str(_python_runtime_root()),
            "/runtime",
            "--ro-bind",
            str(cwd.parent),
            "/inputs",
            "--dir",
            "/work",
            "--setenv",
            "TMPDIR",
            "/tmp",
            "--setenv",
            "PYTHONDONTWRITEBYTECODE",
            "1",
            "--uid",
            "65534",
            "--gid",
            "65534",
            "--chdir",
            f"/inputs/{cwd.name}",
            "--",
            *sandboxed_command,
        )
    if kind == "unshare":
        unshare = shutil.which("unshare")
        if unshare is None:
            raise NetworkSandboxUnavailableError()
        return (unshare, "--user", "--map-root-user", "--net", "--", *command)
    raise NetworkSandboxUnavailableError()


def _run_python_in_network_sandbox(
    command: tuple[str, ...], *, cwd: Path, environment: Mapping[str, str], timeout_seconds: float
) -> subprocess.CompletedProcess[bytes]:
    sandboxed = _network_sandbox_command(command, cwd=cwd)
    try:
        return subprocess.run(
            sandboxed,
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess(
            sandboxed,
            returncode=124,
            stdout=error.stdout or b"",
            stderr=(error.stderr or b"") + b"\ngraded process timed out",
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise NetworkSandboxUnavailableError() from error


def _require_network_sandbox() -> None:
    """Prove the host can create an isolated network namespace before grading begins."""
    if _network_sandbox_kind() is None:
        raise NetworkSandboxUnavailableError()


@functools.lru_cache(maxsize=1)
def _network_sandbox_kind() -> str | None:
    """Prefer a confined bubblewrap root.

    GitHub permits its sudo launcher after user-namespace failure.
    """
    if sys.platform != "linux":
        return None
    candidates = (
        (
            "bubblewrap",
            (
                shutil.which("bwrap"),
                "--unshare-user",
                "--uid",
                "0",
                "--gid",
                "0",
                "--unshare-net",
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--",
                sys.executable,
                "-c",
                "pass",
            ),
        ),
        (
            "sudo_bubblewrap",
            (
                shutil.which("sudo"),
                "-n",
                shutil.which("bwrap"),
                "--unshare-net",
                "--",
                sys.executable,
                "-c",
                "pass",
            ),
        ),
        (
            "unshare",
            (
                shutil.which("unshare"),
                "--user",
                "--map-root-user",
                "--net",
                "--",
                sys.executable,
                "-c",
                "pass",
            ),
        ),
    )
    for kind, command in candidates:
        if command[0] is None:
            continue
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            return kind
    return None


def _python_runtime_root() -> Path:
    return Path(sys.executable).resolve().parent.parent


def _remap_sandbox_command(command: tuple[str, ...], cwd: Path) -> tuple[str, ...]:
    runtime_root = _python_runtime_root()
    input_root = cwd.parent.resolve()
    remapped: list[str] = []
    for value in command:
        path = Path(value)
        if path.is_absolute():
            try:
                remapped.append(str(Path("/runtime") / path.resolve().relative_to(runtime_root)))
                continue
            except ValueError:
                try:
                    remapped.append(str(Path("/inputs") / path.resolve().relative_to(input_root)))
                    continue
                except ValueError:
                    pass
        remapped.append(value)
    return tuple(remapped)


def _resolve_command(
    command: tuple[str, ...],
    *,
    snapshot_root: Path,
    native_tests: Path,
    hidden_tests: Path,
    dependencies: Path,
) -> tuple[str, ...]:
    replacements = {
        "{python}": str(Path(sys.executable).resolve()),
        "{snapshot}": str(snapshot_root),
        "{native_tests}": str(native_tests),
        "{hidden_tests}": str(hidden_tests),
        "{dependencies}": str(dependencies),
    }
    result = tuple(replacements.get(value, value) for value in command)
    if (
        Path(result[0]).resolve() != Path(sys.executable).resolve()
        or len(result) < 2
        or result[1].startswith("-")
    ):
        raise ValueError("grader command must execute a pinned test script")
    return result


def _minimal_grader_environment(root: Path) -> dict[str, str]:
    runtime = {"PATH": os.defpath, "TEMP": str(root), "TMP": str(root)}
    if os.name == "nt":
        runtime["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
        runtime["COMSPEC"] = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    environment = build_process_environment(ProcessRole.GRADER, runtime_environment=runtime)
    environment.update(
        {
            "NO_PROXY": "*",
            "no_proxy": "*",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
        }
    )
    return environment


def _materialize_files(files: Mapping[str, tuple[int, bytes]], root: Path) -> None:
    for relative, (mode, data) in files.items():
        target = root / relative
        resolved_parent = target.parent.resolve()
        if not resolved_parent.is_relative_to(root.resolve()):
            raise SnapshotIntegrityError("workspace snapshot materialization escaped its root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)


def _changed_paths(
    baseline: Mapping[str, tuple[int, bytes]], terminal: Mapping[str, tuple[int, bytes]]
) -> tuple[str, ...]:
    return tuple(
        sorted(
            path
            for path in set(baseline) | set(terminal)
            if baseline.get(path) != terminal.get(path)
        )
    )


def _scope_is_allowed(changed_paths: tuple[str, ...], contract: GraderContract) -> bool:
    for path in changed_paths:
        try:
            normalized_path = _normalized_policy_path(path)
            forbidden = tuple(
                _normalized_policy_path(pattern) for pattern in contract.forbidden_paths
            )
            allowed = tuple(_normalized_policy_path(pattern) for pattern in contract.allowed_paths)
        except ValueError:
            return False
        if any(fnmatch.fnmatchcase(normalized_path, pattern) for pattern in forbidden):
            return False
        if allowed and not any(
            fnmatch.fnmatchcase(normalized_path, pattern) for pattern in allowed
        ):
            return False
    return True


def _safe_relative_path(path: str) -> bool:
    try:
        _normalized_policy_path(path)
    except ValueError:
        return False
    return True


def _normalized_policy_path(path: str) -> str:
    normalized = unicodedata.normalize("NFC", path).replace("\\", "/")
    if not normalized or normalized.startswith("/") or ":" in normalized:
        raise ValueError("path is not relative")
    segments = normalized.split("/")
    if any(
        not segment or segment in {".", ".."} or segment.endswith((" ", "."))
        for segment in segments
    ):
        raise ValueError("path has an ambiguous alias")
    return "/".join(segment.casefold() for segment in segments)
