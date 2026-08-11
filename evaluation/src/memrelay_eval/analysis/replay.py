"""Sealed, credential-free verification of retained analysis and grading evidence."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final

from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.errors import ReproductionError
from memrelay_eval.domain.ids import AttemptId, ProtocolId, RunId

REPRODUCTION_SCHEMA_VERSION: Final = "1.0.0"
ANALYSIS_ABSOLUTE_TOLERANCE: Final = 1e-10
ANALYSIS_RELATIVE_TOLERANCE: Final = 1e-8
GRADER_CONTINUOUS_TOLERANCE: Final = 1e-6
_SHA256_LENGTH: Final = 64
_AUTHORITY_REPLAY_RUNNER_PATH: Final = "inputs/replay-runner.py"
_AUTHORITY_REPLAY_RUNNER: Final = (
    b"from memrelay_eval.analysis.replay import rebuild_retained_outputs\n"
    b"def rebuild(bundle, roots):\n"
    b"    return rebuild_retained_outputs(bundle, roots)\n"
)
_AUTHORITY_REPLAY_RUNNER_SHA256: Final = sha256(_AUTHORITY_REPLAY_RUNNER).hexdigest()


@dataclass(frozen=True, slots=True)
class BundleInput:
    """One immutable CAS or independently verified backup input."""

    root: str
    path: str
    role: str
    sha256: str
    source_sha256: str
    derivation_sha256: str | None


@dataclass(frozen=True, slots=True)
class ReplayOutputs:
    """The outcome projection compared with a sealed reproduction bundle."""

    categories: Mapping[str, int]
    numeric: Mapping[str, float]
    figures: Mapping[str, str]
    grader_binary: Mapping[str, bool]
    grader_tests: Mapping[str, object]
    grader_continuous: Mapping[str, float]
    normalized_evidence: Mapping[str, str]

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> ReplayOutputs:
        _require_exact_keys(document, {"analysis", "grader", "normalized_evidence"}, "outputs")
        analysis = _mapping(document["analysis"], "analysis")
        grader = _mapping(document["grader"], "grader")
        _require_exact_keys(analysis, {"categories", "figures", "numeric"}, "analysis")
        _require_exact_keys(grader, {"binary", "continuous", "tests"}, "grader")
        return cls(
            categories=_string_integer_mapping(analysis["categories"], "analysis.categories"),
            numeric=_string_number_mapping(analysis["numeric"], "analysis.numeric"),
            figures=_string_hash_mapping(analysis["figures"], "analysis.figures"),
            grader_binary=_string_boolean_mapping(grader["binary"], "grader.binary"),
            grader_tests=_string_json_mapping(grader["tests"], "grader.tests"),
            grader_continuous=_string_number_mapping(grader["continuous"], "grader.continuous"),
            normalized_evidence=_string_hash_mapping(
                document["normalized_evidence"], "normalized_evidence"
            ),
        )

    def document(self) -> dict[str, object]:
        return {
            "analysis": {
                "categories": dict(sorted(self.categories.items())),
                "figures": dict(sorted(self.figures.items())),
                "numeric": dict(sorted(self.numeric.items())),
            },
            "grader": {
                "binary": dict(sorted(self.grader_binary.items())),
                "continuous": dict(sorted(self.grader_continuous.items())),
                "tests": dict(sorted(self.grader_tests.items())),
            },
            "normalized_evidence": dict(sorted(self.normalized_evidence.items())),
        }


@dataclass(frozen=True, slots=True)
class ReproductionBundle:
    """Canonical authority for one deterministic, network-off reproduction."""

    bundle_id: str
    protocol_sha256: str
    runtime: Mapping[str, str]
    inputs: tuple[BundleInput, ...]
    normalization: Mapping[str, str]
    runner_path: str
    expected: ReplayOutputs

    @classmethod
    def parse(cls, data: bytes) -> ReproductionBundle:
        document = _canonical_document(data, "reproduction_bundle_not_canonical")
        _require_exact_keys(
            document,
            {
                "artifact_type",
                "bundle_id",
                "expected",
                "inputs",
                "normalization",
                "protocol_sha256",
                "runner_path",
                "runtime",
                "schema_version",
            },
            "reproduction bundle",
        )
        if (
            document["schema_version"] != REPRODUCTION_SCHEMA_VERSION
            or document["artifact_type"] != "reproduction_bundle"
        ):
            raise ReproductionError("reproduction_bundle_schema_invalid")
        bundle_id = _hash(document["bundle_id"], "bundle_id")
        if bundle_id != canonical_digest(
            {key: value for key, value in document.items() if key != "bundle_id"}
        ):
            raise ReproductionError("reproduction_bundle_identity_mismatch")
        protocol_sha256 = _hash(document["protocol_sha256"], "protocol_sha256")
        runtime = _string_mapping(document["runtime"], "runtime")
        if (
            runtime.get("python") != "3.13"
            or runtime.get("duckdb") != "1.5.5"
            or runtime.get("pyarrow") != "25.0.0"
        ):
            raise ReproductionError("reproduction_runtime_not_pinned")
        if "lock_sha256" not in runtime:
            raise ReproductionError("reproduction_runtime_lock_missing")
        _hash(runtime["lock_sha256"], "runtime.lock_sha256")
        normalization = _string_mapping(document["normalization"], "normalization")
        if normalization != {"paths": "relative_posix", "timestamps": "omitted"}:
            raise ReproductionError("reproduction_normalization_policy_invalid")
        raw_inputs = _list(document["inputs"], "inputs")
        if not raw_inputs:
            raise ReproductionError("reproduction_inputs_missing")
        inputs = tuple(_parse_input(value) for value in raw_inputs)
        if len({(item.root, item.path) for item in inputs}) != len(inputs):
            raise ReproductionError("reproduction_input_duplicated")
        required_roles = {
            "analysis_source",
            "evidence_source",
            "grader_contract",
            "replay_runner",
        }
        roles = tuple(item.role for item in inputs)
        if any(roles.count(role) != 1 for role in required_roles):
            raise ReproductionError("reproduction_input_role_authority_conflict")
        runner_path = document["runner_path"]
        if not isinstance(runner_path, str) or not _safe_relative_path(runner_path):
            raise ReproductionError("reproduction_runner_reference_invalid")
        runner_inputs = tuple(
            item for item in inputs if item.role == "replay_runner" and item.path == runner_path
        )
        if len(runner_inputs) != 1:
            raise ReproductionError("reproduction_runner_not_sealed")
        runner = runner_inputs[0]
        if (
            runner_path != _AUTHORITY_REPLAY_RUNNER_PATH
            or runner.sha256 != _AUTHORITY_REPLAY_RUNNER_SHA256
            or runner.source_sha256 != _AUTHORITY_REPLAY_RUNNER_SHA256
            or runner.derivation_sha256 != _AUTHORITY_REPLAY_RUNNER_SHA256
        ):
            raise ReproductionError("reproduction_runner_not_authority_owned")
        return cls(
            bundle_id=bundle_id,
            protocol_sha256=protocol_sha256,
            runtime=runtime,
            inputs=inputs,
            normalization=normalization,
            runner_path=runner_path,
            expected=ReplayOutputs.from_document(_mapping(document["expected"], "expected")),
        )

    def bytes(self) -> bytes:
        return canonical_bytes(
            {
                "artifact_type": "reproduction_bundle",
                "bundle_id": self.bundle_id,
                "protocol_sha256": self.protocol_sha256,
                "expected": self.expected.document(),
                "inputs": [
                    {
                        "derivation_sha256": item.derivation_sha256,
                        "path": item.path,
                        "role": item.role,
                        "root": item.root,
                        "sha256": item.sha256,
                        "source_sha256": item.source_sha256,
                    }
                    for item in self.inputs
                ],
                "normalization": dict(sorted(self.normalization.items())),
                "runner_path": self.runner_path,
                "runtime": dict(sorted(self.runtime.items())),
                "schema_version": REPRODUCTION_SCHEMA_VERSION,
            }
        )

    def input_for(self, role: str) -> BundleInput:
        """Return the sole immutable source permitted to explain one outcome domain."""
        matches = tuple(item for item in self.inputs if item.role == role)
        if len(matches) != 1:
            raise ReproductionError("reproduction_input_role_authority_conflict")
        return matches[0]


@dataclass(frozen=True, slots=True)
class ReplayMismatch:
    """Privacy-minimized first divergence and all affected authoritative outputs."""

    field: str
    expected: object
    actual: object
    source_sha256: str
    derivation_sha256: str | None
    downstream_impacts: tuple[str, ...]

    def document(self) -> dict[str, object]:
        return {
            "actual": self.actual,
            "derivation_sha256": self.derivation_sha256,
            "downstream_impacts": list(self.downstream_impacts),
            "expected": self.expected,
            "field": self.field,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    """Append-only verdict for a deterministic replay."""

    bundle_id: str
    matches: bool
    mismatches: tuple[ReplayMismatch, ...]

    def document(self) -> dict[str, object]:
        document = {
            "artifact_type": "reproduction_comparison",
            "bundle_id": self.bundle_id,
            "matches": self.matches,
            "mismatches": [mismatch.document() for mismatch in self.mismatches],
            "schema_version": REPRODUCTION_SCHEMA_VERSION,
        }
        return {**document, "comparison_sha256": canonical_digest(document)}


@dataclass(frozen=True, slots=True)
class StochasticRerunIdentity:
    """New, non-confirmatory identity with immutable lineage to the prior result."""

    original_attempt_id: AttemptId
    original_protocol_id: ProtocolId
    original_run_id: RunId
    protocol_id: ProtocolId
    run_id: RunId
    attempt_id: AttemptId
    conclusion_class: str
    output_directory: Path

    def document(self) -> dict[str, object]:
        return {
            "artifact_type": "stochastic_rerun_lineage",
            "attempt_id": str(self.attempt_id),
            "conclusion_class": self.conclusion_class,
            "lineage": {
                "original_attempt_id": str(self.original_attempt_id),
                "original_protocol_id": str(self.original_protocol_id),
                "original_run_id": str(self.original_run_id),
            },
            "protocol_id": str(self.protocol_id),
            "run_id": str(self.run_id),
            "schema_version": REPRODUCTION_SCHEMA_VERSION,
        }


def verify_bundle_inputs(
    bundle: ReproductionBundle,
    *,
    cas_root: Path | str,
    backup_root: Path | str | None = None,
) -> None:
    """Verify all named immutable inputs before any replay code can execute."""
    roots = {"cas": Path(cas_root).expanduser().resolve()}
    if backup_root is not None:
        roots["backup"] = Path(backup_root).expanduser().resolve()
    for item in bundle.inputs:
        root = roots.get(item.root)
        if root is None or not root.is_dir():
            raise ReproductionError("reproduction_input_root_unavailable")
        candidate = root / item.path
        if not candidate.is_relative_to(root):
            raise ReproductionError("reproduction_input_path_invalid")
        _, actual = _read_verified_regular_file(candidate)
        if actual != item.sha256:
            raise ReproductionError(
                "reproduction_source_hash_mismatch",
                (
                    ReplayMismatch(
                        field=f"input.{item.path}",
                        expected=item.sha256,
                        actual=actual,
                        source_sha256=item.source_sha256,
                        derivation_sha256=item.derivation_sha256,
                        downstream_impacts=("analysis", "grader", "normalized_evidence"),
                    ),
                ),
            )


def _verified_roots(
    bundle: ReproductionBundle,
    *,
    cas_root: Path | str,
    backup_root: Path | str | None,
) -> dict[str, Path]:
    verify_bundle_inputs(bundle, cas_root=cas_root, backup_root=backup_root)
    roots = {"cas": Path(cas_root).expanduser().resolve()}
    if backup_root is not None:
        roots["backup"] = Path(backup_root).expanduser().resolve()
    return roots


def replay_offline(
    bundle: ReproductionBundle,
    *,
    cas_root: Path | str,
    rebuilder: Callable[[ReproductionBundle], ReplayOutputs],
    backup_root: Path | str | None = None,
) -> ReplayComparison:
    """Compare a supplied deterministic rebuild; production uses ``execute_sealed_replay``."""
    verify_bundle_inputs(bundle, cas_root=cas_root, backup_root=backup_root)
    actual = rebuilder(bundle)
    if not isinstance(actual, ReplayOutputs):
        raise ReproductionError("reproduction_rebuilder_output_invalid")
    verify_bundle_inputs(bundle, cas_root=cas_root, backup_root=backup_root)
    return compare_replay_outputs(bundle, actual)


def execute_sealed_replay(
    bundle: ReproductionBundle,
    *,
    cas_root: Path | str,
    backup_root: Path | str | None = None,
    sandbox_runner: Callable[[ReproductionBundle, Mapping[str, Path]], Mapping[str, object]]
    | None = None,
) -> ReplayComparison:
    """Execute the hash-verified runner in a proven OS network sandbox."""
    roots = _verified_roots(bundle, cas_root=cas_root, backup_root=backup_root)
    document = (sandbox_runner or _run_runner_in_os_sandbox)(bundle, roots)
    actual = ReplayOutputs.from_document(document)
    if sandbox_runner is None:
        actual = _with_replayed_grader(actual, bundle, roots)
    verify_bundle_inputs(bundle, cas_root=cas_root, backup_root=backup_root)
    return compare_replay_outputs(bundle, actual)


def _run_runner_in_os_sandbox(
    bundle: ReproductionBundle, roots: Mapping[str, Path]
) -> Mapping[str, object]:
    """Run the sealed runner in the grader's established namespace/container authority."""
    from memrelay_eval.adapters.grader.executable import (
        _minimal_grader_environment,
        run_in_network_sandbox,
    )
    from memrelay_eval.domain.errors import NetworkSandboxUnavailableError

    with tempfile.TemporaryDirectory(prefix="memrelay-reproduction-") as temporary:
        root = Path(temporary)
        workspace = root / "runtime"
        data_root = root / "data"
        workspace.mkdir()
        shutil.copytree(Path(__file__).parents[1], workspace / "memrelay_eval")
        for item in bundle.inputs:
            source = roots[item.root] / item.path
            destination = data_root / item.root / item.path
            _copy_verified_regular_file(source, destination, item.sha256)
        bundle_path = root / "bundle.json"
        bundle_path.write_bytes(bundle.bytes())
        runner = data_root / "cas" / bundle.runner_path
        if not runner.is_file():
            for item in bundle.inputs:
                if item.path == bundle.runner_path:
                    runner = data_root / item.root / item.path
                    break
        launcher = workspace / "run_replay.py"
        launcher.write_text(
            (
                "import json, runpy, sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, str(Path(__file__).parent))\n"
                "bundle = Path(sys.argv[1]).read_bytes()\n"
                "roots = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
                "namespace = runpy.run_path(sys.argv[3])\n"
                "result = namespace['rebuild'](bundle, roots)\n"
                "print(json.dumps(result, sort_keys=True, separators=(',', ':')))\n"
            ),
            encoding="utf-8",
        )
        environment = _minimal_grader_environment(workspace)
        root_paths = {name: str(data_root / name) for name in roots}
        command = (
            str(Path(sys.executable).resolve()),
            str(launcher),
            str(bundle_path),
            str(root / "roots.json"),
            str(runner),
        )
        (root / "roots.json").write_bytes(canonical_bytes(root_paths))
        try:
            completed = run_in_network_sandbox(
                command,
                cwd=workspace,
                environment=environment,
                timeout_seconds=60,
            )
        except NetworkSandboxUnavailableError as error:
            raise ReproductionError("reproduction_network_sandbox_unavailable") from error
        if completed.returncode != 0:
            raise ReproductionError("reproduction_sandboxed_runner_failed")
        return _canonical_document(completed.stdout, "reproduction_runner_output_not_canonical")


def _read_verified_regular_file(path: Path) -> tuple[bytes, str]:
    """Read one file while rejecting symlinks and path swaps before authority use."""
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ReproductionError("reproduction_input_path_invalid")
        with path.open("rb") as stream:
            data = stream.read()
            after = os.fstat(stream.fileno())
    except OSError as error:
        raise ReproductionError("reproduction_input_path_invalid") from error
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
    ):
        raise ReproductionError("reproduction_input_toctou_detected")
    return data, sha256(data).hexdigest()


def _copy_verified_regular_file(source: Path, destination: Path, expected_sha256: str) -> None:
    data, actual = _read_verified_regular_file(source)
    if actual != expected_sha256:
        raise ReproductionError("reproduction_source_hash_mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    copied, copied_hash = _read_verified_regular_file(destination)
    if copied != data or copied_hash != expected_sha256:
        raise ReproductionError("reproduction_snapshot_copy_mismatch")


def seal_reproduction_bundle(
    *,
    dataset_root: Path | str,
    dataset_version: str,
    queries: tuple[Mapping[str, object], ...],
    grader_result: Mapping[str, object],
    normalized_evidence: Mapping[str, object],
    protocol_sha256: str,
    runtime_lock: Path | str,
    output_root: Path | str,
    backup_receipt: Path | str | None = None,
) -> ReproductionBundle:
    """Copy and bind retained authorities into one immutable, executable replay bundle."""
    protocol_sha256 = _hash(protocol_sha256, "protocol_sha256")
    source_root = Path(dataset_root).expanduser().resolve()
    source_directory = source_root / dataset_version
    if source_directory.is_symlink() or not source_directory.is_dir():
        raise ReproductionError("reproduction_dataset_not_retained")
    root = Path(output_root).expanduser().resolve()
    if root.exists():
        raise ReproductionError("reproduction_bundle_output_exists")
    root.mkdir(parents=True)
    copied_inputs: list[BundleInput] = []
    dataset_target = root / "inputs" / "dataset" / dataset_version
    for source in sorted(source_directory.rglob("*")):
        if source.is_symlink():
            raise ReproductionError("reproduction_input_path_invalid")
        if source.is_dir():
            continue
        relative = source.relative_to(source_directory)
        target = dataset_target / relative
        data, digest = _read_verified_regular_file(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        copied_inputs.append(
            BundleInput(
                root="cas",
                path=(Path("inputs") / "dataset" / dataset_version / relative).as_posix(),
                role="analysis_data",
                sha256=digest,
                source_sha256=digest,
                derivation_sha256=None,
            )
        )
    plan = {
        "artifact_type": "analysis_replay_plan",
        "dataset_root": "inputs/dataset",
        "dataset_version": dataset_version,
        "protocol_sha256": protocol_sha256,
        "queries": [dict(item) for item in queries],
        "schema_version": REPRODUCTION_SCHEMA_VERSION,
    }
    plan_path = root / "inputs" / "analysis-plan.json"
    plan_bytes = canonical_bytes(plan)
    plan_path.write_bytes(plan_bytes)
    copied_inputs.append(
        BundleInput(
            root="cas",
            path="inputs/analysis-plan.json",
            role="analysis_source",
            sha256=sha256(plan_bytes).hexdigest(),
            source_sha256=sha256(plan_bytes).hexdigest(),
            derivation_sha256=canonical_digest(plan),
        )
    )
    grader_descriptor = _parse_grader_replay_descriptor(grader_result)
    grader_path = root / "inputs" / "grader-replay.json"
    grader_bytes = canonical_bytes(dict(grader_result))
    grader_path.write_bytes(grader_bytes)
    copied_inputs.append(
        BundleInput(
            root="cas",
            path="inputs/grader-replay.json",
            role="grader_contract",
            sha256=sha256(grader_bytes).hexdigest(),
            source_sha256=sha256(grader_bytes).hexdigest(),
            derivation_sha256=canonical_digest(grader_result),
        )
    )
    grader_store = root / "inputs" / "grader-cas"
    for artifact in grader_descriptor["artifacts"]:
        artifact_data = base64.b64decode(artifact["content_base64"], validate=True)
        artifact_sha256 = sha256(artifact_data).hexdigest()
        if artifact_sha256 != artifact["sha256"]:
            raise ReproductionError("reproduction_grader_artifact_hash_mismatch")
        artifact_path = (
            grader_store / "blobs" / "sha256" / artifact_sha256[:2] / artifact_sha256[2:]
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(artifact_data)
        copied_inputs.append(
            BundleInput(
                root="cas",
                path=(
                    Path("inputs")
                    / "grader-cas"
                    / "blobs"
                    / "sha256"
                    / artifact_sha256[:2]
                    / artifact_sha256[2:]
                ).as_posix(),
                role="grader_artifact",
                sha256=artifact_sha256,
                source_sha256=artifact_sha256,
                derivation_sha256=None,
            )
        )
    evidence_path = root / "inputs" / "normalized-evidence.json"
    evidence_bytes = canonical_bytes(dict(normalized_evidence))
    evidence_path.write_bytes(evidence_bytes)
    copied_inputs.append(
        BundleInput(
            root="cas",
            path="inputs/normalized-evidence.json",
            role="evidence_source",
            sha256=sha256(evidence_bytes).hexdigest(),
            source_sha256=sha256(evidence_bytes).hexdigest(),
            derivation_sha256=canonical_digest(normalized_evidence),
        )
    )
    lock_data, lock_digest = _read_verified_regular_file(Path(runtime_lock).expanduser().resolve())
    lock_path = root / "inputs" / "runtime.lock"
    lock_path.write_bytes(lock_data)
    copied_inputs.append(
        BundleInput(
            root="cas",
            path="inputs/runtime.lock",
            role="runtime_lock",
            sha256=lock_digest,
            source_sha256=lock_digest,
            derivation_sha256=None,
        )
    )
    if backup_receipt is not None:
        receipt_source = Path(backup_receipt).expanduser().resolve()
        if (
            receipt_source.name != "backup-receipt.json"
            or receipt_source.parent.parent.name != "generations"
        ):
            raise ReproductionError("reproduction_backup_receipt_location_invalid")
        from memrelay_eval.domain.errors import BackupConformanceError
        from memrelay_eval.evidence.backup import verify_backup_generation

        try:
            receipt = verify_backup_generation(
                backup_root=receipt_source.parent.parent.parent,
                generation_id=receipt_source.parent.name,
            )
        except OSError as error:
            raise ReproductionError("reproduction_backup_receipt_unavailable") from error
        except BackupConformanceError as error:
            raise ReproductionError("reproduction_backup_receipt_invalid") from error
        receipt_data, receipt_digest = _read_verified_regular_file(receipt_source)
        if receipt_data != receipt.bytes():
            raise ReproductionError("reproduction_backup_receipt_invalid")
        receipt_path = root / "inputs" / "backup-receipt.json"
        receipt_path.write_bytes(receipt_data)
        copied_inputs.append(
            BundleInput(
                root="cas",
                path="inputs/backup-receipt.json",
                role="backup_receipt",
                sha256=receipt_digest,
                source_sha256=receipt_digest,
                derivation_sha256=None,
            )
        )
    runner_path = root / _AUTHORITY_REPLAY_RUNNER_PATH
    runner_bytes = _AUTHORITY_REPLAY_RUNNER
    runner_path.write_bytes(runner_bytes)
    copied_inputs.append(
        BundleInput(
            root="cas",
            path=_AUTHORITY_REPLAY_RUNNER_PATH,
            role="replay_runner",
            sha256=sha256(runner_bytes).hexdigest(),
            source_sha256=sha256(runner_bytes).hexdigest(),
            derivation_sha256=_AUTHORITY_REPLAY_RUNNER_SHA256,
        )
    )
    roots = {"cas": root}
    expected = _supplement_replay_outputs(
        _rebuild_retained_plan(plan, roots, protocol_sha256), tuple(copied_inputs), roots
    )
    document = {
        "artifact_type": "reproduction_bundle",
        "expected": expected.document(),
        "inputs": [_input_document(item) for item in copied_inputs],
        "normalization": {"paths": "relative_posix", "timestamps": "omitted"},
        "protocol_sha256": protocol_sha256,
        "runner_path": _AUTHORITY_REPLAY_RUNNER_PATH,
        "runtime": {
            "duckdb": "1.5.5",
            "lock_sha256": lock_digest,
            "pyarrow": "25.0.0",
            "python": "3.13",
        },
        "schema_version": REPRODUCTION_SCHEMA_VERSION,
    }
    document["bundle_id"] = canonical_digest(document)
    payload = canonical_bytes(document)
    bundle_path = root / "reproduction-bundle.json"
    _write_immutable(bundle_path, payload, "reproduction_bundle_publish_conflict")
    return ReproductionBundle.parse(payload)


def rebuild_retained_outputs(bundle_bytes: bytes, roots: Mapping[str, str]) -> dict[str, object]:
    """Rebuild standard analysis, grader, and normalized-evidence outputs from sealed files."""
    bundle = ReproductionBundle.parse(bundle_bytes)
    root_paths = {name: Path(path).resolve() for name, path in roots.items()}
    for item in bundle.inputs:
        path = root_paths[item.root] / item.path
        _, digest = _read_verified_regular_file(path)
        if digest != item.sha256:
            raise ReproductionError("reproduction_source_hash_mismatch")
    plan_item = bundle.input_for("analysis_source")
    plan = _canonical_document(
        (root_paths[plan_item.root] / plan_item.path).read_bytes(),
        "reproduction_plan_not_canonical",
    )
    analysis = _rebuild_retained_plan(plan, root_paths, bundle.protocol_sha256)
    evidence = _normalized_evidence_hash(bundle.inputs, root_paths)
    return ReplayOutputs(
        categories=analysis.categories,
        numeric=analysis.numeric,
        figures=analysis.figures,
        grader_binary={},
        grader_tests={},
        grader_continuous={},
        normalized_evidence={"evidence": evidence},
    ).document()


def _rebuild_retained_plan(
    plan: Mapping[str, object], roots: Mapping[str, Path], protocol_sha256: str
) -> ReplayOutputs:
    from memrelay_eval.analysis.queries import (
        AnalysisQuery,
        ReadOnlyDuckDbAnalysis,
        deterministic_figure_svg,
    )

    _require_exact_keys(
        plan,
        {
            "artifact_type",
            "dataset_root",
            "dataset_version",
            "protocol_sha256",
            "queries",
            "schema_version",
        },
        "analysis replay plan",
    )
    if (
        plan["artifact_type"] != "analysis_replay_plan"
        or plan["schema_version"] != REPRODUCTION_SCHEMA_VERSION
        or plan["protocol_sha256"] != protocol_sha256
    ):
        raise ReproductionError("reproduction_analysis_plan_authority_conflict")
    dataset_root = plan["dataset_root"]
    dataset_version = plan["dataset_version"]
    if not isinstance(dataset_root, str) or not isinstance(dataset_version, str):
        raise ReproductionError("reproduction_analysis_plan_invalid")
    cas = roots.get("cas")
    if cas is None:
        raise ReproductionError("reproduction_input_root_unavailable")
    query_documents = _list(plan["queries"], "queries")
    categories: dict[str, int] = {}
    numeric: dict[str, float] = {}
    figures: dict[str, str] = {}
    with ReadOnlyDuckDbAnalysis.open(cas / dataset_root, dataset_version) as analysis:
        if protocol_sha256 not in analysis.dataset.manifest["protocol_sha256"]:
            raise ReproductionError("reproduction_dataset_protocol_conflict")
        for value in query_documents:
            query = _mapping(value, "query")
            _require_exact_keys(
                query,
                {"columns", "equals", "figure_columns", "name", "numeric_column", "table"},
                "query",
            )
            name = query["name"]
            if not isinstance(name, str) or not name:
                raise ReproductionError("reproduction_analysis_plan_invalid")
            columns = tuple(_string_list(query["columns"], "query.columns"))
            if len(set(columns)) != len(columns):
                raise ReproductionError("reproduction_analysis_plan_invalid")
            equals: list[tuple[str, str]] = []
            for raw_equals in _list(query["equals"], "query.equals"):
                equals_item = _mapping(raw_equals, "query.equals")
                _require_exact_keys(equals_item, {"column", "value"}, "query.equals")
                column, value = equals_item["column"], equals_item["value"]
                if not isinstance(column, str) or not column or not isinstance(value, str):
                    raise ReproductionError("reproduction_analysis_plan_invalid")
                equals.append((column, value))
            table_name = query["table"]
            if not isinstance(table_name, str) or not table_name:
                raise ReproductionError("reproduction_analysis_plan_invalid")
            table = analysis.read(AnalysisQuery(table_name, columns, tuple(equals)))
            categories[name] = table.num_rows
            numeric_column = query["numeric_column"]
            if not isinstance(numeric_column, str) or numeric_column not in table.column_names:
                raise ReproductionError("reproduction_numeric_column_invalid")
            numeric[name] = sum(
                float(value) for value in table[numeric_column].to_pylist() if value is not None
            )
            figure_columns = tuple(_string_list(query["figure_columns"], "query.figure_columns"))
            figures[name] = sha256(deterministic_figure_svg(table, figure_columns)).hexdigest()
    return ReplayOutputs(
        categories=categories,
        numeric=numeric,
        figures=figures,
        grader_binary={},
        grader_tests={},
        grader_continuous={},
        normalized_evidence={},
    )


def _supplement_replay_outputs(
    analysis: ReplayOutputs, inputs: tuple[BundleInput, ...], roots: Mapping[str, Path]
) -> ReplayOutputs:
    by_role: dict[str, BundleInput] = {}
    for item in inputs:
        if item.role in {"grader_contract", "evidence_source"}:
            if item.role in by_role:
                raise ReproductionError("reproduction_input_role_authority_conflict")
            by_role[item.role] = item
    try:
        grader_input = by_role["grader_contract"]
        by_role["evidence_source"]
    except KeyError as error:
        raise ReproductionError("reproduction_input_role_authority_conflict") from error
    grader = _canonical_document(
        (roots[grader_input.root] / grader_input.path).read_bytes(),
        "reproduction_grader_replay_not_canonical",
    )
    descriptor = _parse_grader_replay_descriptor(grader)
    expected_grader = _mapping(descriptor["expected"], "grader expected")
    return ReplayOutputs(
        categories=analysis.categories,
        numeric=analysis.numeric,
        figures=analysis.figures,
        grader_binary=_string_boolean_mapping(expected_grader["binary"], "grader.binary"),
        grader_tests=_string_json_mapping(expected_grader["tests"], "grader.tests"),
        grader_continuous=_string_number_mapping(
            expected_grader["continuous"], "grader.continuous"
        ),
        normalized_evidence={"evidence": _normalized_evidence_hash(inputs, roots)},
    )


def _normalized_evidence_hash(inputs: tuple[BundleInput, ...], roots: Mapping[str, Path]) -> str:
    evidence_input = next((item for item in inputs if item.role == "evidence_source"), None)
    if evidence_input is None:
        raise ReproductionError("reproduction_input_role_authority_conflict")
    evidence = _canonical_document(
        (roots[evidence_input.root] / evidence_input.path).read_bytes(),
        "reproduction_evidence_not_canonical",
    )
    return sha256(canonical_bytes(_normalize_evidence(evidence))).hexdigest()


def build_grader_replay_descriptor(
    *,
    snapshot: object,
    contract: object,
    expected_result: object,
    artifact_store: object,
) -> dict[str, object]:
    """Retain every immutable byte the production executable grader needs to re-run."""
    from memrelay_eval.adapters.workspace.base import WorkspaceSnapshot
    from memrelay_eval.domain.entities import GraderContract, GraderResult

    if (
        not isinstance(snapshot, WorkspaceSnapshot)
        or not isinstance(contract, GraderContract)
        or not isinstance(expected_result, GraderResult)
        or not hasattr(artifact_store, "open_verified")
    ):
        raise ReproductionError("reproduction_grader_inputs_invalid")
    refs = tuple(
        ref
        for ref in (
            snapshot.baseline_files_artifact,
            snapshot.terminal_files_artifact,
            snapshot.patch_artifact,
            snapshot.canonical_artifact,
            contract.native_tests_artifact,
            contract.hidden_tests_artifact,
            contract.dependencies_artifact,
        )
        if ref is not None
    )
    if len(refs) != 7 or len({ref.sha256 for ref in refs}) != len(refs):
        raise ReproductionError("reproduction_grader_inputs_incomplete")
    artifacts: list[dict[str, object]] = []
    for ref in refs:
        data = artifact_store.open_verified(ref)
        if sha256(data).hexdigest() != ref.sha256:
            raise ReproductionError("reproduction_grader_artifact_hash_mismatch")
        artifacts.append(
            {
                "content_base64": base64.b64encode(data).decode("ascii"),
                "sha256": ref.sha256,
                "size_bytes": ref.size_bytes,
            }
        )
    return {
        "artifact_type": "grader_replay_inputs",
        "artifacts": artifacts,
        "contract": {
            **contract.to_record(),
            "dependencies_size_bytes": contract.dependencies_artifact.size_bytes,
            "hidden_tests_size_bytes": contract.hidden_tests_artifact.size_bytes,
            "native_tests_size_bytes": contract.native_tests_artifact.size_bytes,
        },
        "expected": {
            "binary": {"passed": expected_result.binary_passed},
            "continuous": {"score": expected_result.continuous_score},
            "tests": dict(expected_result.test_outcomes),
        },
        "schema_version": REPRODUCTION_SCHEMA_VERSION,
        "snapshot": {
            "baseline_files_sha256": snapshot.baseline_files_artifact.sha256,
            "baseline_files_size_bytes": snapshot.baseline_files_artifact.size_bytes,
            "baseline_revision": snapshot.baseline_revision,
            "canonical_sha256": snapshot.canonical_sha256,
            "canonical_size_bytes": snapshot.canonical_artifact.size_bytes,
            "patch_sha256": snapshot.patch_artifact.sha256,
            "patch_size_bytes": snapshot.patch_artifact.size_bytes,
            "revision": snapshot.revision,
            "source_content_sha256": snapshot.source_content_sha256,
            "terminal_files_sha256": snapshot.terminal_files_artifact.sha256,
            "terminal_files_size_bytes": snapshot.terminal_files_artifact.size_bytes,
            "workspace_content_sha256": snapshot.workspace_content_sha256,
        },
    }


def _parse_grader_replay_descriptor(value: Mapping[str, object]) -> Mapping[str, object]:
    _require_exact_keys(
        value,
        {"artifact_type", "artifacts", "contract", "expected", "schema_version", "snapshot"},
        "grader replay",
    )
    if (
        value["artifact_type"] != "grader_replay_inputs"
        or value["schema_version"] != REPRODUCTION_SCHEMA_VERSION
    ):
        raise ReproductionError("reproduction_grader_replay_schema_invalid")
    expected = _mapping(value["expected"], "grader expected")
    _require_exact_keys(expected, {"binary", "continuous", "tests"}, "grader expected")
    binary = _string_boolean_mapping(expected["binary"], "grader.binary")
    continuous = _string_number_mapping(expected["continuous"], "grader.continuous")
    tests = _string_boolean_mapping(expected["tests"], "grader.tests")
    if set(binary) != {"passed"} or set(continuous) != {"score"} or not tests:
        raise ReproductionError("reproduction_grader_expected_invalid")
    artifacts = _list(value["artifacts"], "grader artifacts")
    if len(artifacts) != 7:
        raise ReproductionError("reproduction_grader_inputs_incomplete")
    seen: set[str] = set()
    for raw in artifacts:
        artifact = _mapping(raw, "grader artifact")
        _require_exact_keys(artifact, {"content_base64", "sha256", "size_bytes"}, "grader artifact")
        digest = _hash(artifact["sha256"], "grader artifact.sha256")
        if (
            digest in seen
            or not isinstance(artifact["size_bytes"], int)
            or artifact["size_bytes"] < 0
        ):
            raise ReproductionError("reproduction_grader_artifact_invalid")
        if not isinstance(artifact["content_base64"], str):
            raise ReproductionError("reproduction_grader_artifact_invalid")
        try:
            data = base64.b64decode(artifact["content_base64"], validate=True)
        except ValueError as error:
            raise ReproductionError("reproduction_grader_artifact_invalid") from error
        if len(data) != artifact["size_bytes"] or sha256(data).hexdigest() != digest:
            raise ReproductionError("reproduction_grader_artifact_hash_mismatch")
        seen.add(digest)
    contract = _mapping(value["contract"], "grader contract")
    snapshot = _mapping(value["snapshot"], "grader snapshot")
    _require_exact_keys(
        contract,
        {
            "allowed_paths",
            "command",
            "dependencies_sha256",
            "dependencies_size_bytes",
            "expected_baseline_files_sha256",
            "expected_baseline_revision",
            "forbidden_paths",
            "grader_sha256",
            "grader_version",
            "hidden_tests_sha256",
            "hidden_tests_size_bytes",
            "maximum_regrades",
            "native_tests_sha256",
            "native_tests_size_bytes",
            "network_policy",
            "schema_version",
            "scope_policy_sha256",
            "tamper_policy_sha256",
        },
        "grader contract",
    )
    _require_exact_keys(
        snapshot,
        {
            "baseline_files_sha256",
            "baseline_files_size_bytes",
            "baseline_revision",
            "canonical_sha256",
            "canonical_size_bytes",
            "patch_sha256",
            "patch_size_bytes",
            "revision",
            "source_content_sha256",
            "terminal_files_sha256",
            "terminal_files_size_bytes",
            "workspace_content_sha256",
        },
        "grader snapshot",
    )
    required_artifacts = {
        contract["dependencies_sha256"],
        contract["hidden_tests_sha256"],
        contract["native_tests_sha256"],
        snapshot["baseline_files_sha256"],
        snapshot["canonical_sha256"],
        snapshot["patch_sha256"],
        snapshot["terminal_files_sha256"],
    }
    if not all(isinstance(item, str) and item in seen for item in required_artifacts):
        raise ReproductionError("reproduction_grader_inputs_incomplete")
    return value


def _with_replayed_grader(
    outputs: ReplayOutputs, bundle: ReproductionBundle, roots: Mapping[str, Path]
) -> ReplayOutputs:
    descriptor = _grader_descriptor_from_bundle(bundle, roots)
    grader = _execute_retained_grader(descriptor)
    return ReplayOutputs(
        categories=outputs.categories,
        numeric=outputs.numeric,
        figures=outputs.figures,
        grader_binary={"passed": grader["binary_passed"]},
        grader_tests=grader["tests"],
        grader_continuous={"score": grader["continuous_score"]},
        normalized_evidence=outputs.normalized_evidence,
    )


def _grader_descriptor_from_bundle(
    bundle: ReproductionBundle, roots: Mapping[str, Path]
) -> Mapping[str, object]:
    item = bundle.input_for("grader_contract")
    data, digest = _read_verified_regular_file(roots[item.root] / item.path)
    if digest != item.sha256:
        raise ReproductionError("reproduction_source_hash_mismatch")
    return _parse_grader_replay_descriptor(
        _canonical_document(data, "reproduction_grader_replay_not_canonical")
    )


def _execute_retained_grader(descriptor: Mapping[str, object]) -> Mapping[str, object]:
    """Use the established executable grader and its own OS network sandbox."""
    from memrelay_eval.adapters.artifacts.filesystem import FilesystemArtifactStore
    from memrelay_eval.adapters.grader.executable import CredentialFreeExecutableGrader
    from memrelay_eval.adapters.workspace.base import WorkspaceSnapshot
    from memrelay_eval.domain.entities import ArtifactRef, GraderContract
    from memrelay_eval.domain.states import GraderTerminalKind

    artifacts = {
        item["sha256"]: base64.b64decode(item["content_base64"], validate=True)
        for raw in _list(descriptor["artifacts"], "grader artifacts")
        for item in (_mapping(raw, "grader artifact"),)
    }
    contract_document = _mapping(descriptor["contract"], "grader contract")
    snapshot_document = _mapping(descriptor["snapshot"], "grader snapshot")
    with tempfile.TemporaryDirectory(prefix="memrelay-replay-grader-") as temporary:
        store = FilesystemArtifactStore(Path(temporary) / "cas")

        def artifact(digest_key: str, size_key: str) -> ArtifactRef:
            digest = contract_document.get(digest_key, snapshot_document.get(digest_key))
            size = contract_document.get(size_key, snapshot_document.get(size_key))
            if not isinstance(digest, str) or not isinstance(size, int):
                raise ReproductionError("reproduction_grader_inputs_incomplete")
            data = artifacts.get(digest)
            if data is None or len(data) != size:
                raise ReproductionError("reproduction_grader_artifact_missing")
            reference = store.put_bytes(
                data,
                media_type="application/octet-stream",
                classification="reproduction_grader_input",
            )
            if reference.sha256 != digest:
                raise ReproductionError("reproduction_grader_artifact_hash_mismatch")
            return reference

        snapshot = WorkspaceSnapshot(
            revision=str(snapshot_document["revision"]),
            source_content_sha256=str(snapshot_document["source_content_sha256"]),
            workspace_content_sha256=str(snapshot_document["workspace_content_sha256"]),
            baseline_revision=str(snapshot_document["baseline_revision"]),
            baseline_files_artifact=artifact("baseline_files_sha256", "baseline_files_size_bytes"),
            terminal_files_artifact=artifact("terminal_files_sha256", "terminal_files_size_bytes"),
            patch_artifact=artifact("patch_sha256", "patch_size_bytes"),
            canonical_artifact=artifact("canonical_sha256", "canonical_size_bytes"),
            canonical_sha256=str(snapshot_document["canonical_sha256"]),
        )
        contract = GraderContract(
            grader_version=str(contract_document["grader_version"]),
            grader_sha256=str(contract_document["grader_sha256"]),
            native_tests_artifact=artifact("native_tests_sha256", "native_tests_size_bytes"),
            hidden_tests_artifact=artifact("hidden_tests_sha256", "hidden_tests_size_bytes"),
            dependencies_artifact=artifact("dependencies_sha256", "dependencies_size_bytes"),
            scope_policy_sha256=str(contract_document["scope_policy_sha256"]),
            tamper_policy_sha256=str(contract_document["tamper_policy_sha256"]),
            command=tuple(_string_list(contract_document["command"], "grader command")),
            allowed_paths=tuple(
                _string_list(contract_document["allowed_paths"], "grader allowed paths")
            ),
            forbidden_paths=tuple(
                _string_list(contract_document["forbidden_paths"], "grader forbidden paths")
            ),
            expected_baseline_revision=str(contract_document["expected_baseline_revision"]),
            expected_baseline_files_sha256=str(contract_document["expected_baseline_files_sha256"]),
            network_policy=str(contract_document["network_policy"]),
            maximum_regrades=contract_document["maximum_regrades"],
        )
        result = asyncio.run(CredentialFreeExecutableGrader(store).grade(snapshot, contract))
    if (
        result.terminal not in {GraderTerminalKind.PASSED, GraderTerminalKind.FAILED}
        or result.binary_passed is None
        or result.continuous_score is None
    ):
        raise ReproductionError("reproduction_grader_execution_failed")
    return {
        "binary_passed": result.binary_passed,
        "continuous_score": result.continuous_score,
        "tests": dict(result.test_outcomes),
    }


def _normalize_evidence(value: object) -> object:
    """Normalize only path spellings and timestamps; retain every substantive field."""
    if isinstance(value, list):
        return [_normalize_evidence(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if key.endswith("_at") or key in {"timestamp", "timestamps"}:
            continue
        if key.endswith("_path") and isinstance(item, str):
            path = item.replace("\\", "/")
            if path.startswith("/") or ":" in path or ".." in Path(path).parts:
                raise ReproductionError("reproduction_evidence_path_not_relative")
            normalized[key] = path
        else:
            normalized[key] = _normalize_evidence(item)
    return normalized


def _string_list(value: object, name: str) -> list[str]:
    values = _list(value, name)
    if any(not isinstance(item, str) or not item for item in values):
        raise ReproductionError(f"{name.replace('.', '_')}_invalid")
    return values


def _input_document(item: BundleInput) -> dict[str, object]:
    return {
        "derivation_sha256": item.derivation_sha256,
        "path": item.path,
        "role": item.role,
        "root": item.root,
        "sha256": item.sha256,
        "source_sha256": item.source_sha256,
    }


def compare_replay_outputs(bundle: ReproductionBundle, actual: ReplayOutputs) -> ReplayComparison:
    """Compare the three authority classes without normalizing substantive changes."""
    expected = bundle.expected
    impacts = {
        "categories": ("analysis",),
        "numeric": ("analysis",),
        "figures": ("analysis",),
        "grader.binary": ("grader",),
        "grader.tests": ("grader",),
        "grader.continuous": ("grader",),
        "normalized_evidence": ("normalized_evidence",),
    }
    mismatches: list[ReplayMismatch] = []
    _compare_exact_mapping(
        mismatches,
        "categories",
        expected.categories,
        actual.categories,
        bundle.input_for("analysis_source"),
        impacts["categories"],
    )
    _compare_numeric_mapping(
        mismatches,
        expected.numeric,
        actual.numeric,
        bundle.input_for("analysis_source"),
        impacts["numeric"],
    )
    _compare_exact_mapping(
        mismatches,
        "figures",
        expected.figures,
        actual.figures,
        bundle.input_for("analysis_source"),
        impacts["figures"],
    )
    _compare_exact_mapping(
        mismatches,
        "grader.binary",
        expected.grader_binary,
        actual.grader_binary,
        bundle.input_for("grader_contract"),
        impacts["grader.binary"],
    )
    _compare_exact_mapping(
        mismatches,
        "grader.tests",
        expected.grader_tests,
        actual.grader_tests,
        bundle.input_for("grader_contract"),
        impacts["grader.tests"],
    )
    _compare_continuous_mapping(
        mismatches,
        expected.grader_continuous,
        actual.grader_continuous,
        bundle.input_for("grader_contract"),
        impacts["grader.continuous"],
    )
    _compare_exact_mapping(
        mismatches,
        "normalized_evidence",
        expected.normalized_evidence,
        actual.normalized_evidence,
        bundle.input_for("evidence_source"),
        impacts["normalized_evidence"],
    )
    return ReplayComparison(bundle.bundle_id, not mismatches, tuple(mismatches))


def publish_comparison(comparison: ReplayComparison, output_root: Path | str) -> Path:
    """Publish one content-addressed verdict without mutating earlier evidence."""
    document = comparison.document()
    payload = canonical_bytes(document)
    target = (
        Path(output_root).expanduser().resolve() / "reproductions" / document["comparison_sha256"]
    )
    target.mkdir(parents=True, exist_ok=True)
    path = target / "reproduction-comparison.json"
    _write_immutable(path, payload, "reproduction_comparison_conflict")
    return path


def allocate_stochastic_rerun(
    *,
    original_protocol_id: str,
    original_run_id: str,
    original_attempt_id: str,
    conclusion_class: str,
    output_root: Path | str,
    original_evidence_root: Path | str,
) -> StochasticRerunIdentity:
    """Allocate a distinct protocol/run/attempt namespace for non-confirmatory work."""
    if conclusion_class not in {"null", "harm", "indeterminate", "positive"}:
        raise ReproductionError("stochastic_conclusion_class_invalid")
    original_root = Path(original_evidence_root).expanduser().resolve()
    root = Path(output_root).expanduser().resolve()
    if root == original_root or root.is_relative_to(original_root):
        raise ReproductionError("stochastic_output_overlaps_confirmatory_evidence")
    identity = StochasticRerunIdentity(
        original_protocol_id=ProtocolId(original_protocol_id),
        original_run_id=RunId(original_run_id),
        original_attempt_id=AttemptId(original_attempt_id),
        protocol_id=ProtocolId.new(),
        run_id=RunId.new(),
        attempt_id=AttemptId.new(),
        conclusion_class=conclusion_class,
        output_directory=Path(),
    )
    target = (
        root
        / "stochastic-reruns"
        / str(identity.protocol_id)
        / str(identity.run_id)
        / str(identity.attempt_id)
    )
    if target.exists():
        raise ReproductionError("stochastic_identity_collision")
    target.mkdir(parents=True)
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ReproductionError("stochastic_output_path_invalid")
    identity = StochasticRerunIdentity(
        original_protocol_id=identity.original_protocol_id,
        original_run_id=identity.original_run_id,
        original_attempt_id=identity.original_attempt_id,
        protocol_id=identity.protocol_id,
        run_id=identity.run_id,
        attempt_id=identity.attempt_id,
        conclusion_class=identity.conclusion_class,
        output_directory=resolved,
    )
    _write_immutable(
        resolved / "lineage.json",
        canonical_bytes(identity.document()),
        "stochastic_lineage_conflict",
    )
    return identity


def _parse_input(value: object) -> BundleInput:
    document = _mapping(value, "input")
    _require_exact_keys(
        document, {"derivation_sha256", "path", "role", "root", "sha256", "source_sha256"}, "input"
    )
    root = document["root"]
    path = document["path"]
    role = document["role"]
    if (
        root not in {"cas", "backup"}
        or role
        not in {
            "analysis_data",
            "analysis_source",
            "backup_receipt",
            "evidence_source",
            "grader_artifact",
            "grader_contract",
            "replay_runner",
            "runtime_lock",
        }
        or not isinstance(path, str)
        or not _safe_relative_path(path)
    ):
        raise ReproductionError("reproduction_input_reference_invalid")
    derivation = document["derivation_sha256"]
    return BundleInput(
        root=root,
        path=path,
        role=role,
        sha256=_hash(document["sha256"], "input.sha256"),
        source_sha256=_hash(document["source_sha256"], "input.source_sha256"),
        derivation_sha256=None
        if derivation is None
        else _hash(derivation, "input.derivation_sha256"),
    )


def _compare_exact_mapping(
    mismatches: list[ReplayMismatch],
    field: str,
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    source: BundleInput,
    impacts: tuple[str, ...],
) -> None:
    for name in sorted(set(expected).union(actual)):
        if expected.get(name) != actual.get(name):
            mismatches.append(
                ReplayMismatch(
                    f"{field}.{name}",
                    expected.get(name),
                    actual.get(name),
                    source.source_sha256,
                    source.derivation_sha256,
                    impacts,
                )
            )


def _compare_numeric_mapping(
    mismatches: list[ReplayMismatch],
    expected: Mapping[str, float],
    actual: Mapping[str, float],
    source: BundleInput,
    impacts: tuple[str, ...],
) -> None:
    for name in sorted(set(expected).union(actual)):
        left, right = expected.get(name), actual.get(name)
        if (
            left is None
            or right is None
            or not math.isclose(
                left,
                right,
                rel_tol=ANALYSIS_RELATIVE_TOLERANCE,
                abs_tol=ANALYSIS_ABSOLUTE_TOLERANCE,
            )
        ):
            mismatches.append(
                ReplayMismatch(
                    f"numeric.{name}",
                    left,
                    right,
                    source.source_sha256,
                    source.derivation_sha256,
                    impacts,
                )
            )


def _compare_continuous_mapping(
    mismatches: list[ReplayMismatch],
    expected: Mapping[str, float],
    actual: Mapping[str, float],
    source: BundleInput,
    impacts: tuple[str, ...],
) -> None:
    for name in sorted(set(expected).union(actual)):
        left, right = expected.get(name), actual.get(name)
        if (
            left is None
            or right is None
            or not math.isclose(left, right, rel_tol=0.0, abs_tol=GRADER_CONTINUOUS_TOLERANCE)
        ):
            mismatches.append(
                ReplayMismatch(
                    f"grader.continuous.{name}",
                    left,
                    right,
                    source.source_sha256,
                    source.derivation_sha256,
                    impacts,
                )
            )


def _write_immutable(path: Path, payload: bytes, conflict_code: str) -> None:
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ReproductionError(conflict_code)
        return
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    except OSError as error:
        if path.is_file() and path.read_bytes() == payload:
            return
        raise ReproductionError(conflict_code) from error
    finally:
        if temporary.exists():
            temporary.unlink()


def _canonical_document(data: bytes, code: str) -> Mapping[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ReproductionError(code)
            result[key] = value
        return result

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReproductionError(code) from error
    if not isinstance(document, dict) or canonical_bytes(document) != data:
        raise ReproductionError(code)
    return document


def _require_exact_keys(document: Mapping[str, object], keys: set[str], name: str) -> None:
    if set(document) != keys:
        raise ReproductionError(f"{name.replace(' ', '_')}_keys_invalid")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReproductionError(f"{name.replace('.', '_')}_invalid")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ReproductionError(f"{name}_invalid")
    return value


def _string_mapping(value: object, name: str) -> dict[str, str]:
    document = _mapping(value, name)
    if any(
        not isinstance(key, str) or not isinstance(item, str) or not item
        for key, item in document.items()
    ):
        raise ReproductionError(f"{name.replace('.', '_')}_invalid")
    return dict(document)


def _string_json_mapping(value: object, name: str) -> dict[str, object]:
    document = _mapping(value, name)
    if any(not isinstance(key, str) or not _json_safe(item) for key, item in document.items()):
        raise ReproductionError(f"{name.replace('.', '_')}_invalid")
    return dict(document)


def _string_integer_mapping(value: object, name: str) -> dict[str, int]:
    document = _mapping(value, name)
    if any(
        not isinstance(key, str) or not isinstance(item, int) or isinstance(item, bool)
        for key, item in document.items()
    ):
        raise ReproductionError(f"{name.replace('.', '_')}_invalid")
    return dict(document)


def _string_boolean_mapping(value: object, name: str) -> dict[str, bool]:
    document = _mapping(value, name)
    if any(
        not isinstance(key, str) or not isinstance(item, bool) for key, item in document.items()
    ):
        raise ReproductionError(f"{name.replace('.', '_')}_invalid")
    return dict(document)


def _string_number_mapping(value: object, name: str) -> dict[str, float]:
    document = _mapping(value, name)
    result: dict[str, float] = {}
    for key, item in document.items():
        if not isinstance(key, str) or not isinstance(item, (int, float)) or isinstance(item, bool):
            raise ReproductionError(f"{name.replace('.', '_')}_invalid")
        number = float(item)
        if not math.isfinite(number):
            raise ReproductionError(f"{name.replace('.', '_')}_invalid")
        result[key] = number
    return result


def _string_hash_mapping(value: object, name: str) -> dict[str, str]:
    return {key: _hash(item, name) for key, item in _string_mapping(value, name).items()}


def _hash(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ReproductionError(f"{name.replace('.', '_')}_invalid")
    return value


def _safe_relative_path(path: str) -> bool:
    candidate = Path(path)
    return (
        bool(path)
        and not candidate.is_absolute()
        and ".." not in candidate.parts
        and "\\" not in path
    )


def _json_safe(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    return False
