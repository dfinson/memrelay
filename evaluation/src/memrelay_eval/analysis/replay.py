"""Sealed, credential-free verification of retained analysis and grading evidence."""

from __future__ import annotations

import json
import math
import os
import runpy
import socket
import subprocess
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
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
_CREDENTIAL_FRAGMENTS: Final = ("token", "secret", "password", "api_key", "credential")
_MINIMAL_ENVIRONMENT_NAMES: Final = (
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
)


class NetworkAccessDenied(RuntimeError):
    """Raised by the in-process replay guard before a DNS or socket operation."""


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
        runner_path = document["runner_path"]
        if not isinstance(runner_path, str) or not _safe_relative_path(runner_path):
            raise ReproductionError("reproduction_runner_reference_invalid")
        if not any(item.role == "replay_runner" and item.path == runner_path for item in inputs):
            raise ReproductionError("reproduction_runner_not_sealed")
        return cls(
            bundle_id=bundle_id,
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
        candidate = (root / item.path).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file() or candidate.is_symlink():
            raise ReproductionError("reproduction_input_path_invalid")
        actual = sha256(candidate.read_bytes()).hexdigest()
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


def _path_for_bundle_input(
    bundle: ReproductionBundle, roots: Mapping[str, Path], path: str
) -> Path:
    for item in bundle.inputs:
        if item.path == path:
            return (roots[item.root] / item.path).resolve()
    raise ReproductionError("reproduction_runner_not_sealed")


def replay_offline(
    bundle: ReproductionBundle,
    *,
    cas_root: Path | str,
    rebuilder: Callable[[ReproductionBundle], ReplayOutputs],
    backup_root: Path | str | None = None,
) -> ReplayComparison:
    """Run the supplied deterministic rebuilder with credentials and network unavailable."""
    verify_bundle_inputs(bundle, cas_root=cas_root, backup_root=backup_root)
    try:
        with network_off():
            actual = rebuilder(bundle)
    except NetworkAccessDenied as error:
        raise ReproductionError("reproduction_network_access_denied") from error
    if not isinstance(actual, ReplayOutputs):
        raise ReproductionError("reproduction_rebuilder_output_invalid")
    verify_bundle_inputs(bundle, cas_root=cas_root, backup_root=backup_root)
    return compare_replay_outputs(bundle, actual)


def execute_sealed_replay(
    bundle: ReproductionBundle,
    *,
    cas_root: Path | str,
    backup_root: Path | str | None = None,
) -> ReplayComparison:
    """Execute only the hash-verified bundle runner in the offline process boundary."""
    roots = _verified_roots(bundle, cas_root=cas_root, backup_root=backup_root)
    runner = _path_for_bundle_input(bundle, roots, bundle.runner_path)

    def rebuild(_: ReproductionBundle) -> ReplayOutputs:
        namespace = runpy.run_path(str(runner))
        function = namespace.get("rebuild")
        if not callable(function):
            raise ReproductionError("reproduction_runner_entrypoint_missing")
        document = function(bundle.bytes(), {name: str(root) for name, root in roots.items()})
        if not isinstance(document, Mapping):
            raise ReproductionError("reproduction_runner_output_invalid")
        return ReplayOutputs.from_document(document)

    return replay_offline(
        bundle,
        cas_root=cas_root,
        backup_root=backup_root,
        rebuilder=rebuild,
    )


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


@contextmanager
def network_off() -> Iterator[None]:
    """Deny DNS/socket access and expose a minimal credential-free environment."""
    saved_environment = dict(os.environ)
    environment = {
        name: value
        for name, value in saved_environment.items()
        if name in _MINIMAL_ENVIRONMENT_NAMES and not _looks_like_credential(name)
    }
    original = (
        socket.create_connection,
        socket.getaddrinfo,
        socket.gethostbyname,
        socket.gethostbyname_ex,
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.socket.sendto,
        subprocess.Popen,
    )
    os_process_entrypoints = {
        name: getattr(os, name)
        for name in dir(os)
        if name == "system" or name.startswith(("exec", "posix_spawn", "spawn")) or name == "popen"
    }

    def denied(*_: object, **__: object) -> object:
        raise NetworkAccessDenied("network access is denied during offline reproduction")

    os.environ.clear()
    os.environ.update(environment)
    socket.create_connection = denied  # type: ignore[assignment]
    socket.getaddrinfo = denied  # type: ignore[assignment]
    socket.gethostbyname = denied  # type: ignore[assignment]
    socket.gethostbyname_ex = denied  # type: ignore[assignment]
    socket.socket.connect = denied  # type: ignore[method-assign]
    socket.socket.connect_ex = denied  # type: ignore[method-assign]
    socket.socket.sendto = denied  # type: ignore[method-assign]
    subprocess.Popen = denied  # type: ignore[assignment]
    for name in os_process_entrypoints:
        setattr(os, name, denied)
    try:
        yield
    finally:
        (
            socket.create_connection,
            socket.getaddrinfo,
            socket.gethostbyname,
            socket.gethostbyname_ex,
            socket.socket.connect,
            socket.socket.connect_ex,
            socket.socket.sendto,
            subprocess.Popen,
        ) = original
        for name, function in os_process_entrypoints.items():
            setattr(os, name, function)
        os.environ.clear()
        os.environ.update(saved_environment)


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
        or role not in {"analysis_source", "evidence_source", "grader_contract", "replay_runner"}
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


def _looks_like_credential(name: str) -> bool:
    lowered = name.casefold()
    return lowered in {"gh_token", "github_token"} or any(
        fragment in lowered for fragment in _CREDENTIAL_FRAGMENTS
    )
