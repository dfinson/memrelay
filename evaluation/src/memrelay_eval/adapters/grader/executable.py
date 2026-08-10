"""Credential-free executable grading over detached immutable workspace bytes."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from memrelay_eval.adapters.process.environment import ProcessRole, build_process_environment
from memrelay_eval.adapters.workspace.base import WorkspaceSnapshot
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.entities import ArtifactRef, GraderContract, GraderResult
from memrelay_eval.domain.errors import ArtifactIntegrityError, SnapshotIntegrityError
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
            completed = _run_python_with_network_denied(
                command,
                cwd=snapshot_root,
                environment=environment,
                timeout_seconds=self._timeout_seconds,
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
                self,
                snapshot,
                contract_sha256,
                completed,
                raw_artifact,
                input_evidence,
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
        self, snapshot: WorkspaceSnapshot, contract_sha256: str, reason: str
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
            (marker,),
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
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    if completed.returncode == 0:
        tests = _test_outcomes(payload, default=True)
        score = _score(payload)
        components = _components(payload)
        terminal = GraderTerminalKind.PASSED if all(tests.values()) else GraderTerminalKind.FAILED
        return grader._result(
            snapshot,
            contract_sha256,
            terminal,
            terminal is GraderTerminalKind.PASSED,
            tests,
            score,
            components,
            raw_output,
            (*input_evidence, raw_output),
        )
    if payload:
        tests = _test_outcomes(payload, default=False)
        return grader._result(
            snapshot,
            contract_sha256,
            GraderTerminalKind.FAILED,
            False,
            tests,
            _score(payload),
            _components(payload),
            raw_output,
            (*input_evidence, raw_output),
        )
    marker = grader._artifact_store.put_bytes(
        canonical_bytes({"terminal": "unavailable", "reason": "grader_crash"}),
        media_type="application/json",
        classification="grader_result",
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


def _resolve_command(
    command: tuple[str, ...],
    *,
    snapshot_root: Path,
    native_tests: Path,
    hidden_tests: Path,
    dependencies: Path,
) -> tuple[str, ...]:
    replacements = {
        "{python}": sys.executable,
        "{snapshot}": str(snapshot_root),
        "{native_tests}": str(native_tests),
        "{hidden_tests}": str(hidden_tests),
        "{dependencies}": str(dependencies),
    }
    result = tuple(replacements.get(value, value) for value in command)
    if result[0] != sys.executable or len(result) < 2 or result[1].startswith("-"):
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


def _run_python_with_network_denied(
    command: tuple[str, ...], *, cwd: Path, environment: Mapping[str, str], timeout_seconds: float
) -> subprocess.CompletedProcess[bytes]:
    target, *arguments = command[1:]
    bootstrap = (
        "import runpy,socket,sys;"
        "deny=lambda *args,**kwargs: (_ for _ in ()).throw("
        "OSError('network denied by frozen grader contract'));"
        "socket.create_connection=deny;socket.getaddrinfo=deny;socket.socket.connect=deny;"
        "sys.argv=[sys.argv[1],*sys.argv[2:]];runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    try:
        return subprocess.run(
            (sys.executable, "-c", bootstrap, target, *arguments),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess(
            command,
            returncode=124,
            stdout=error.stdout or b"",
            stderr=(error.stderr or b"") + b"\ngraded process timed out",
        )


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
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in contract.forbidden_paths):
            return False
        if contract.allowed_paths and not any(
            fnmatch.fnmatchcase(path, pattern) for pattern in contract.allowed_paths
        ):
            return False
    return True


def _safe_relative_path(path: str) -> bool:
    return (
        bool(path) and "\\" not in path and not path.startswith("/") and ".." not in path.split("/")
    )


def _test_outcomes(payload: object, *, default: bool) -> dict[str, bool]:
    if not isinstance(payload, Mapping):
        return {"command": default}
    tests = payload.get("tests")
    if not isinstance(tests, Mapping):
        return {"command": default}
    result = {str(name): value for name, value in tests.items() if isinstance(value, bool)}
    return result or {"command": default}


def _score(payload: object) -> float | None:
    value = payload.get("continuous_score") if isinstance(payload, Mapping) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _components(payload: object) -> dict[str, float]:
    values = payload.get("objective_components") if isinstance(payload, Mapping) else None
    if not isinstance(values, Mapping):
        return {}
    return {
        str(name): float(value)
        for name, value in values.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
