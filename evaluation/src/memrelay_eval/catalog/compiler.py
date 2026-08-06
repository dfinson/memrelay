"""Deterministic, offline compilation and atomic publication of catalog artifacts."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from memrelay_eval.domain.errors import DomainError

from .canonical import attach_digest, canonical_bytes, verify_digest
from .loader import SourceLocation
from .validation import CatalogValidationError, CatalogValidationResult, validate_catalog

GENERATOR_VERSION = "1.0.0"
COMPILED_SCHEMA_VERSION = "1.0.0"
LOCK_SCHEMA_VERSION = "1.0.0"
COMMAND_MANIFEST_SCHEMA_VERSION = "1.0.0"
UNPAID_CONFORMANCE = True
_TRANSACTION_SCHEMA_VERSION = "1.0.0"

_GENERATED_FILENAMES = {
    "tasks": "tasks.json",
    "assignment_inputs": "assignment-inputs.json",
    "fixture_manifest": "fixture-manifest.json",
    "traceability": "traceability.json",
}
_SCENARIO_REFERENCE_FIELDS = (
    ("protocol_ids", "protocols"),
    ("fixture_refs", "fixtures"),
    ("risk_ids", "risks"),
    ("gate_ids", "gates"),
    ("endpoint_ids", "endpoints"),
    ("expected_evidence", "evidence"),
    ("claim_ids", "claims"),
)
_TRACE_SCENARIO_FIELDS = (
    "title",
    "priority",
    "owner",
    "preconditions",
    "injected_conditions",
    "procedure",
    "pass_criteria",
    "allowed_retries",
    "data_classification",
    "network_policy",
    "resource_limits",
)
TerminalStatus = Literal["succeeded", "failed", "interrupted"]


class CatalogCompileError(DomainError):
    """The validated catalog could not become a complete immutable artifact set."""


class CatalogCompileBusyError(CatalogCompileError):
    """Another process owns the catalog compiler transaction lock."""


class CatalogRecoveryError(CatalogCompileError):
    """An interrupted catalog publication cannot be recovered safely."""


@dataclass(frozen=True, slots=True)
class CompilationResult:
    """Verified hashes for one atomically published catalog compilation."""

    catalog_input_sha256: str
    source_sha256: str
    output_sha256: Mapping[str, str]
    protocol_ids: tuple[str, ...]
    runtime_lock: Mapping[str, str | None]
    recovery_status: str


@dataclass(frozen=True, slots=True)
class CompileCommandResult:
    """The typed, manifest-backed result returned by the CLI command."""

    terminal_status: TerminalStatus
    exit_code: int
    compilation: CompilationResult | None
    error: CatalogValidationError | CatalogCompileError | None


class _CatalogPublicationLock:
    """A non-blocking native advisory lock shared by Windows and POSIX processes."""

    def __init__(self, catalog_root: Path) -> None:
        self._path = catalog_root.parent / f".{catalog_root.name}-compile.lock"
        self._handle: Any | None = None

    def __enter__(self) -> _CatalogPublicationLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a+b")
        try:
            self._acquire()
        except OSError as error:
            self._handle.close()
            self._handle = None
            raise CatalogCompileBusyError(
                "catalog compilation is already owned by another process"
            ) from error
        return self

    def __exit__(self, *_: object) -> None:
        if self._handle is None:
            return
        try:
            self._release()
        finally:
            self._handle.close()
            self._handle = None

    def _acquire(self) -> None:
        assert self._handle is not None
        if os.name == "nt":
            self._handle.seek(0)
            if self._handle.read(1) == b"":
                self._handle.seek(0)
                self._handle.write(b"\0")
                self._handle.flush()
                os.fsync(self._handle.fileno())
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(self) -> None:
        assert self._handle is not None
        if os.name == "nt":
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)


def compile_catalog(
    catalog_path: Path,
    *,
    output_dir: Path,
    lock_path: Path,
    prior_lock: Path | None = None,
    runtime_lock: Path | None = None,
    before_publish: Callable[[Path], None] | None = None,
    after_backup: Callable[[Path], None] | None = None,
    after_live: Callable[[Path], None] | None = None,
) -> CompilationResult:
    """Validate, compile, verify, and atomically publish one complete catalog set."""

    catalog_root = _catalog_root(catalog_path, output_dir, lock_path)
    with _CatalogPublicationLock(catalog_root):
        recovery_status = _recover_incomplete_publication(catalog_root)
        return _compile_catalog_locked(
            catalog_path,
            output_dir=output_dir,
            lock_path=lock_path,
            prior_lock=prior_lock,
            runtime_lock=runtime_lock,
            before_publish=before_publish,
            after_backup=after_backup,
            after_live=after_live,
            catalog_root=catalog_root,
            recovery_status=recovery_status,
        )


def _compile_catalog_locked(
    catalog_path: Path,
    *,
    output_dir: Path,
    lock_path: Path,
    prior_lock: Path | None,
    runtime_lock: Path | None,
    before_publish: Callable[[Path], None] | None,
    after_backup: Callable[[Path], None] | None,
    after_live: Callable[[Path], None] | None,
    catalog_root: Path,
    recovery_status: str,
) -> CompilationResult:
    effective_prior_lock = prior_lock
    if effective_prior_lock is None and lock_path.exists():
        effective_prior_lock = lock_path
    validated = validate_catalog(
        catalog_path,
        prior_lock=effective_prior_lock,
        repository_root=catalog_root,
    )
    source_sha256 = _catalog_source_sha256(catalog_path)
    runtime_lock_reference = _runtime_lock_reference(runtime_lock)
    documents = _compiled_documents(
        validated,
        catalog_input_sha256=_sha256(canonical_bytes(validated.catalog)),
        source_sha256=source_sha256,
        runtime_lock=runtime_lock_reference,
    )
    relative_output_dir = output_dir.resolve().relative_to(catalog_root)
    relative_lock_path = lock_path.resolve().relative_to(catalog_root)
    transaction_id = uuid4().hex
    staging = _new_sibling_staging_directory(catalog_root, transaction_id)
    backup = _backup_path(catalog_root, transaction_id)
    transaction = _transaction_path(catalog_root, transaction_id)
    try:
        _write_transaction(
            catalog_root,
            transaction_id=transaction_id,
            staging=staging,
            backup=backup,
            source_sha256=source_sha256,
            prior_lock_sha256=_optional_file_sha256(effective_prior_lock),
            runtime_lock=runtime_lock_reference,
        )
        shutil.copytree(catalog_root, staging, dirs_exist_ok=True)
        staged_output_dir = staging / relative_output_dir
        staged_lock_path = staging / relative_lock_path
        if staged_output_dir.exists():
            shutil.rmtree(staged_output_dir)
        if staged_lock_path.exists():
            staged_lock_path.unlink()
        staged_output_dir.mkdir(parents=True)
        output_sha256 = _write_generated_documents(staged_output_dir, documents)
        lock_document = _catalog_lock_document(
            validated,
            catalog_input_sha256=_sha256(canonical_bytes(validated.catalog)),
            source_sha256=source_sha256,
            output_sha256=output_sha256,
            runtime_lock=runtime_lock_reference,
        )
        _write_canonical_document(staged_lock_path, lock_document)
        output_sha256 = {
            **{f"generated/{filename}": digest for filename, digest in output_sha256.items()},
            "catalog-lock.json": _sha256(staged_lock_path.read_bytes()),
        }
        _verify_compiled_catalog(
            staged_output_dir,
            staged_lock_path,
            catalog_path=staging / catalog_path.resolve().relative_to(catalog_root),
            validated=validated,
            expected_runtime_lock=runtime_lock_reference,
        )
        _require_same_volume(staging, catalog_root)
        if before_publish is not None:
            before_publish(staging)
        _publish_catalog_root(
            catalog_root,
            staging,
            backup=backup,
            after_backup=after_backup,
            after_live=after_live,
        )
        _verify_compiled_catalog(
            output_dir,
            lock_path,
            catalog_path=catalog_path,
            validated=validated,
            expected_runtime_lock=runtime_lock_reference,
        )
        _remove_path(backup)
    except BaseException:
        if backup.exists():
            _restore_catalog_root(catalog_root, backup)
        raise
    finally:
        if catalog_root.exists() and not backup.exists():
            _remove_path(staging)
            _remove_path(transaction)

    return CompilationResult(
        catalog_input_sha256=_sha256(canonical_bytes(validated.catalog)),
        source_sha256=source_sha256,
        output_sha256=output_sha256,
        protocol_ids=tuple(_protocol_ids(validated.catalog)),
        runtime_lock=runtime_lock_reference,
        recovery_status=recovery_status,
    )


def compile_catalog_command(
    catalog_path: Path,
    *,
    output_dir: Path,
    lock_path: Path,
    manifest_path: Path,
    prior_lock: Path | None = None,
    runtime_lock: Path | None = None,
    before_publish: Callable[[Path], None] | None = None,
    after_backup: Callable[[Path], None] | None = None,
    after_live: Callable[[Path], None] | None = None,
) -> CompileCommandResult:
    """Run the compiler and always write a redacted, typed command manifest."""

    try:
        _validate_manifest_destination(
            manifest_path,
            catalog_path,
            output_dir,
            lock_path,
            prior_lock=prior_lock,
            runtime_lock=runtime_lock,
        )
    except CatalogCompileError as error:
        return CompileCommandResult("failed", 1, None, error)
    source_sha256 = _optional_catalog_source_sha256(catalog_path)
    runtime_lock_reference = _runtime_lock_reference_or_missing(runtime_lock)
    try:
        compilation = compile_catalog(
            catalog_path,
            output_dir=output_dir,
            lock_path=lock_path,
            prior_lock=prior_lock,
            runtime_lock=runtime_lock,
            before_publish=before_publish,
            after_backup=after_backup,
            after_live=after_live,
        )
    except KeyboardInterrupt:
        _write_command_manifest(
            manifest_path,
            terminal_status="interrupted",
            catalog_source_sha256=source_sha256,
            catalog_input_sha256=None,
            output_sha256={},
            protocol_ids=(),
            runtime_lock=runtime_lock_reference,
            error_code="interrupted",
        )
        return CompileCommandResult("interrupted", 130, None, None)
    except CatalogValidationError as error:
        _write_command_manifest(
            manifest_path,
            terminal_status="failed",
            catalog_source_sha256=source_sha256,
            catalog_input_sha256=None,
            output_sha256={},
            protocol_ids=(),
            runtime_lock=runtime_lock_reference,
            error_code="catalog_validation",
        )
        return CompileCommandResult("failed", 1, None, error)
    except Exception as error:
        compile_error = (
            error if isinstance(error, CatalogCompileError) else CatalogCompileError(str(error))
        )
        _write_command_manifest(
            manifest_path,
            terminal_status="failed",
            catalog_source_sha256=source_sha256,
            catalog_input_sha256=None,
            output_sha256={},
            protocol_ids=(),
            runtime_lock=runtime_lock_reference,
            error_code="catalog_compile",
        )
        return CompileCommandResult("failed", 1, None, compile_error)
    except BaseException:
        interruption = CatalogCompileError("catalog compilation was interrupted")
        _write_command_manifest(
            manifest_path,
            terminal_status="interrupted",
            catalog_source_sha256=source_sha256,
            catalog_input_sha256=None,
            output_sha256={},
            protocol_ids=(),
            runtime_lock=runtime_lock_reference,
            error_code="interrupted",
        )
        return CompileCommandResult("interrupted", 130, None, interruption)

    _write_command_manifest(
        manifest_path,
        terminal_status="succeeded",
        catalog_source_sha256=compilation.source_sha256,
        catalog_input_sha256=compilation.catalog_input_sha256,
        output_sha256=compilation.output_sha256,
        protocol_ids=compilation.protocol_ids,
        runtime_lock=compilation.runtime_lock,
        error_code=None,
        recovery_status=compilation.recovery_status,
    )
    return CompileCommandResult("succeeded", 0, compilation, None)


def verify_compiled_catalog(
    output_dir: Path,
    lock_path: Path,
    *,
    catalog_path: Path | None = None,
    prior_lock: Path | None = None,
    runtime_lock: Path | None = None,
) -> None:
    """Reject every non-canonical, tampered, incomplete, or mismatched artifact."""

    catalog_root = lock_path.resolve().parent
    with _CatalogPublicationLock(catalog_root):
        _recover_incomplete_publication(catalog_root)
        _verify_compiled_catalog(
            output_dir,
            lock_path,
            catalog_path=catalog_path,
            prior_lock=prior_lock,
            expected_runtime_lock=_runtime_lock_reference(runtime_lock),
        )


def _compiled_documents(
    validated: CatalogValidationResult,
    *,
    catalog_input_sha256: str,
    source_sha256: str,
    runtime_lock: Mapping[str, str | None],
) -> dict[str, Mapping[str, object]]:
    catalog = validated.catalog
    catalog_id = _required_string(catalog, "catalog_id")
    task_records: list[dict[str, object]] = []
    for scenario_index, scenario_value in enumerate(_required_list(catalog, "scenarios")):
        scenario = _required_mapping(scenario_value, f"scenarios/{scenario_index}")
        task_records.append(
            attach_digest(
                {
                    "schema_version": COMPILED_SCHEMA_VERSION,
                    "generator_version": GENERATOR_VERSION,
                    "catalog_id": catalog_id,
                    "scenario_id": _required_string(scenario, "id"),
                    "protocol_ids": list(_required_list(scenario, "protocol_ids")),
                    "fixture_refs": list(_required_list(scenario, "fixture_refs")),
                    "task_input": {
                        "title": _required_string(scenario, "title"),
                        "priority": _required_string(scenario, "priority"),
                        "owner": _required_string(scenario, "owner"),
                        "preconditions": list(_required_list(scenario, "preconditions")),
                        "injected_conditions": list(
                            _required_list(scenario, "injected_conditions")
                        ),
                        "procedure": _required_mapping(scenario.get("procedure"), "procedure"),
                        "expected_evidence": list(_required_list(scenario, "expected_evidence")),
                        "pass_criteria": _required_mapping(
                            scenario.get("pass_criteria"), "pass_criteria"
                        ),
                        "allowed_retries": scenario["allowed_retries"],
                        "risk_ids": list(_required_list(scenario, "risk_ids")),
                        "gate_ids": list(_required_list(scenario, "gate_ids")),
                        "endpoint_ids": list(_required_list(scenario, "endpoint_ids")),
                        "claim_ids": list(_required_list(scenario, "claim_ids")),
                        "data_classification": _required_string(scenario, "data_classification"),
                        "network_policy": _required_string(scenario, "network_policy"),
                        "resource_limits": _required_mapping(
                            scenario.get("resource_limits"), "resource_limits"
                        ),
                        "grader_ref": _required_string(scenario, "grader_ref"),
                    },
                    "fixture_validation": "not_performed",
                    "eligibility_evaluation": "not_performed",
                    "unpaid_conformance": UNPAID_CONFORMANCE,
                }
            )
        )
    tasks_document = attach_digest(
        {
            "schema_version": COMPILED_SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "catalog_id": catalog_id,
            "catalog_input_sha256": catalog_input_sha256,
            "source_sha256": source_sha256,
            "runtime_lock": dict(runtime_lock),
            "unpaid_conformance": UNPAID_CONFORMANCE,
            "tasks": task_records,
        }
    )
    assignment_document = attach_digest(
        {
            "schema_version": COMPILED_SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "catalog_id": catalog_id,
            "catalog_input_sha256": catalog_input_sha256,
            "unpaid_conformance": UNPAID_CONFORMANCE,
            "assignment_inputs": [
                attach_digest(
                    {
                        "schema_version": COMPILED_SCHEMA_VERSION,
                        "catalog_id": catalog_id,
                        "scenario_id": task["scenario_id"],
                        "protocol_ids": task["protocol_ids"],
                        "task_digest": task["digest"],
                        "unpaid_conformance": UNPAID_CONFORMANCE,
                    }
                )
                for task in task_records
            ],
        }
    )
    fixture_document = attach_digest(
        {
            "schema_version": COMPILED_SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "catalog_id": catalog_id,
            "catalog_input_sha256": catalog_input_sha256,
            "fixture_content_validation": "not_performed",
            "unpaid_conformance": UNPAID_CONFORMANCE,
            "fixtures": [
                attach_digest(
                    {
                        "schema_version": COMPILED_SCHEMA_VERSION,
                        "catalog_id": catalog_id,
                        "fixture": _required_mapping(fixture, "fixture"),
                        "fixture_content_validation": "not_performed",
                        "unpaid_conformance": UNPAID_CONFORMANCE,
                    }
                )
                for fixture in _required_list(
                    _required_mapping(catalog.get("references"), "references"), "fixtures"
                )
            ],
        }
    )
    traceability_document = attach_digest(
        {
            "schema_version": COMPILED_SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "catalog_id": catalog_id,
            "catalog_input_sha256": catalog_input_sha256,
            "unpaid_conformance": UNPAID_CONFORMANCE,
            "traceability": _traceability_records(validated, task_records),
        }
    )
    return {
        _GENERATED_FILENAMES["tasks"]: tasks_document,
        _GENERATED_FILENAMES["assignment_inputs"]: assignment_document,
        _GENERATED_FILENAMES["fixture_manifest"]: fixture_document,
        _GENERATED_FILENAMES["traceability"]: traceability_document,
    }


def _traceability_records(
    validated: CatalogValidationResult, task_records: list[dict[str, object]]
) -> list[dict[str, object]]:
    catalog = validated.catalog
    references = _required_mapping(catalog.get("references"), "references")
    declarations = _reference_declaration_locations(validated.locations, references)
    records: list[dict[str, object]] = []
    for scenario_index, scenario_value in enumerate(_required_list(catalog, "scenarios")):
        scenario = _required_mapping(scenario_value, f"scenarios/{scenario_index}")
        prefix = f"/scenarios/{scenario_index}"
        links: list[dict[str, object]] = []
        for field, namespace in _SCENARIO_REFERENCE_FIELDS:
            for value_index, identifier in enumerate(_required_list(scenario, field)):
                links.append(
                    {
                        "relation": field,
                        "id": identifier,
                        "scenario_source": _location_document(
                            validated.locations, f"{prefix}/{field}/{value_index}"
                        ),
                        "catalog_source": declarations[namespace][
                            _required_string_value(identifier)
                        ],
                    }
                )
        grader = _required_string(scenario, "grader_ref")
        links.append(
            {
                "relation": "grader_ref",
                "id": grader,
                "scenario_source": _location_document(validated.locations, f"{prefix}/grader_ref"),
                "catalog_source": declarations["graders"][grader],
            }
        )
        field_locations = {
            field: _location_document(validated.locations, f"{prefix}/{field}")
            for field in _TRACE_SCENARIO_FIELDS
        }
        records.append(
            attach_digest(
                {
                    "schema_version": COMPILED_SCHEMA_VERSION,
                    "catalog_id": _required_string(catalog, "catalog_id"),
                    "scenario_id": _required_string(scenario, "id"),
                    "task_digest": task_records[scenario_index]["digest"],
                    "scenario_source": _location_document(validated.locations, prefix),
                    "field_locations": field_locations,
                    "references": links,
                    "fixture_content_validation": "not_performed",
                    "eligibility_evaluation": "not_performed",
                    "unpaid_conformance": UNPAID_CONFORMANCE,
                }
            )
        )
    return records


def _reference_declaration_locations(
    locations: Mapping[str, SourceLocation], references: Mapping[str, object]
) -> dict[str, dict[str, dict[str, object]]]:
    declarations: dict[str, dict[str, dict[str, object]]] = {}
    for namespace, values in references.items():
        if not isinstance(namespace, str) or not isinstance(values, list):
            continue
        namespace_locations: dict[str, dict[str, object]] = {}
        for index, value in enumerate(values):
            if isinstance(value, Mapping):
                identifier = _required_string(value, "id")
                pointer = f"/references/{namespace}/{index}/id"
            else:
                identifier = _required_string_value(value)
                pointer = f"/references/{namespace}/{index}"
            namespace_locations[identifier] = _location_document(locations, pointer)
        declarations[namespace] = namespace_locations
    return declarations


def _catalog_lock_document(
    validated: CatalogValidationResult,
    *,
    catalog_input_sha256: str,
    source_sha256: str,
    output_sha256: Mapping[str, str],
    runtime_lock: Mapping[str, str | None],
) -> dict[str, object]:
    catalog = validated.catalog
    return attach_digest(
        {
            "schema_version": LOCK_SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "catalog_id": _required_string(catalog, "catalog_id"),
            "catalog_version": _required_string(catalog, "catalog_version"),
            "catalog_input_sha256": catalog_input_sha256,
            "source_sha256": source_sha256,
            "semantic_source": dict(validated.semantic_source),
            "change_kind": validated.change_kind,
            "protocol_ids": _protocol_ids(catalog),
            "runtime_lock": dict(runtime_lock),
            "generated_outputs": {
                filename: {
                    "path": f"generated/{filename}",
                    "sha256": digest,
                }
                for filename, digest in output_sha256.items()
            },
            "unpaid_conformance": UNPAID_CONFORMANCE,
        }
    )


def _write_generated_documents(
    output_dir: Path, documents: Mapping[str, Mapping[str, object]]
) -> dict[str, str]:
    output_sha256: dict[str, str] = {}
    for filename, document in documents.items():
        destination = output_dir / filename
        _write_canonical_document(destination, document)
        output_sha256[filename] = _sha256(destination.read_bytes())
    return output_sha256


def _write_canonical_document(path: Path, document: Mapping[str, object]) -> None:
    if not verify_digest(document):
        raise CatalogCompileError(f"generated document for {path.name} has an invalid digest")
    _write_bytes_durable(path, canonical_bytes(document))


def _verify_compiled_catalog(
    output_dir: Path,
    lock_path: Path,
    *,
    catalog_path: Path | None = None,
    prior_lock: Path | None = None,
    validated: CatalogValidationResult | None = None,
    expected_runtime_lock: Mapping[str, str | None] | None = None,
) -> None:
    lock, lock_bytes = _read_canonical_document(lock_path)
    generated_outputs = _required_mapping(lock.get("generated_outputs"), "generated_outputs")
    expected_names = set(_GENERATED_FILENAMES.values())
    if set(generated_outputs) != expected_names:
        raise CatalogCompileError(
            "catalog lock does not declare the complete generated artifact set"
        )
    for filename in sorted(expected_names):
        output, raw = _read_canonical_document(output_dir / filename)
        expected = _required_mapping(generated_outputs.get(filename), filename)
        if expected.get("path") != f"generated/{filename}":
            raise CatalogCompileError(f"catalog lock path is invalid for {filename}")
        if expected.get("sha256") != _sha256(raw):
            raise CatalogCompileError(f"catalog lock hash mismatch for {filename}")
        _verify_record_digests(output, filename)
    if catalog_path is not None:
        expected_validation = validated or validate_catalog(
            catalog_path,
            prior_lock=prior_lock,
            repository_root=catalog_path.parent,
        )
        catalog_input_sha256 = _sha256(canonical_bytes(expected_validation.catalog))
        source_sha256 = _catalog_source_sha256(catalog_path)
        if catalog_input_sha256 != _required_string(lock, "catalog_input_sha256"):
            raise CatalogCompileError("catalog lock canonical input hash does not match the source")
        if source_sha256 != _required_string(lock, "source_sha256"):
            raise CatalogCompileError("catalog lock source hash does not match the source bytes")
        if dict(expected_validation.semantic_source) != _required_mapping(
            lock.get("semantic_source"), "semantic_source"
        ):
            raise CatalogCompileError("catalog lock semantic source does not match the source")
        expected_documents = _compiled_documents(
            expected_validation,
            catalog_input_sha256=catalog_input_sha256,
            source_sha256=source_sha256,
            runtime_lock=expected_runtime_lock or {"path": None, "sha256": None},
        )
        expected_output_sha256: dict[str, str] = {}
        for filename, expected_document in expected_documents.items():
            actual = (output_dir / filename).read_bytes()
            expected_bytes = canonical_bytes(expected_document)
            expected_output_sha256[filename] = _sha256(expected_bytes)
            if actual != expected_bytes:
                raise CatalogCompileError(
                    f"generated document {filename} does not match deterministic compilation"
                )
        expected_lock = _catalog_lock_document(
            expected_validation,
            catalog_input_sha256=catalog_input_sha256,
            source_sha256=source_sha256,
            output_sha256=expected_output_sha256,
            runtime_lock=expected_runtime_lock or {"path": None, "sha256": None},
        )
        if lock_bytes != canonical_bytes(expected_lock):
            raise CatalogCompileError("catalog lock does not match deterministic compilation")


def _read_canonical_document(path: Path) -> tuple[Mapping[str, object], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogCompileError(f"cannot read generated document {path.name}") from error
    if not isinstance(value, Mapping):
        raise CatalogCompileError(f"generated document {path.name} must be an object")
    document = dict(value)
    if raw != canonical_bytes(document):
        raise CatalogCompileError(f"generated document {path.name} is not canonical RFC 8785 JSON")
    if not verify_digest(document):
        raise CatalogCompileError(f"generated document {path.name} has an invalid digest")
    return document, raw


def _verify_record_digests(document: Mapping[str, object], filename: str) -> None:
    collection_name = {
        "tasks.json": "tasks",
        "assignment-inputs.json": "assignment_inputs",
        "fixture-manifest.json": "fixtures",
        "traceability.json": "traceability",
    }[filename]
    records = _required_list(document, collection_name)
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or not verify_digest(record):
            raise CatalogCompileError(f"{filename} record {index} has an invalid digest")


def _new_sibling_staging_directory(catalog_root: Path, transaction_id: str) -> Path:
    staging = catalog_root.parent / f".{catalog_root.name}-compile-stage-{transaction_id}"
    staging.mkdir()
    _fsync_directory(staging.parent)
    return staging


def _backup_path(catalog_root: Path, transaction_id: str) -> Path:
    return catalog_root.parent / f".{catalog_root.name}-compile-backup-{transaction_id}"


def _transaction_path(catalog_root: Path, transaction_id: str) -> Path:
    return catalog_root.parent / f".{catalog_root.name}-compile-transaction-{transaction_id}.json"


def _write_transaction(
    catalog_root: Path,
    *,
    transaction_id: str,
    staging: Path,
    backup: Path,
    source_sha256: str,
    prior_lock_sha256: str | None,
    runtime_lock: Mapping[str, str | None],
) -> Path:
    transaction = _transaction_path(catalog_root, transaction_id)
    document = attach_digest(
        {
            "schema_version": _TRANSACTION_SCHEMA_VERSION,
            "transaction_id": transaction_id,
            "catalog_root": catalog_root.name,
            "staging": staging.name,
            "backup": backup.name,
            "source_sha256": source_sha256,
            "prior_lock_sha256": prior_lock_sha256,
            "runtime_lock": dict(runtime_lock),
        }
    )
    _write_bytes_durable(transaction, canonical_bytes(document))
    return transaction


def _recover_incomplete_publication(catalog_root: Path) -> str:
    parent = catalog_root.parent
    transactions = sorted(parent.glob(f".{catalog_root.name}-compile-transaction-*.json"))
    staging_paths = sorted(parent.glob(f".{catalog_root.name}-compile-stage-*"))
    backup_paths = sorted(parent.glob(f".{catalog_root.name}-compile-backup-*"))
    if not transactions:
        if staging_paths or backup_paths:
            raise CatalogRecoveryError(
                "catalog recovery failed: orphaned publication state has no ownership journal"
            )
        return "not_needed"
    if len(transactions) != 1:
        raise CatalogRecoveryError(
            "catalog recovery failed: multiple publication journals make ownership ambiguous"
        )

    transaction = transactions[0]
    document = _read_transaction(transaction, catalog_root)
    staging = parent / _required_string(document, "staging")
    backup = parent / _required_string(document, "backup")
    if set(staging_paths) - {staging} or set(backup_paths) - {backup}:
        raise CatalogRecoveryError(
            "catalog recovery failed: transaction state does not uniquely own "
            "staging and backup paths"
        )

    root_exists = catalog_root.exists()
    staging_exists = staging.exists()
    backup_exists = backup.exists()
    runtime_lock = _transaction_runtime_lock(document)
    if not backup_exists and root_exists:
        _remove_path(staging)
        _remove_path(transaction)
        return "discarded_unpublished" if staging_exists else "completed_live"
    if backup_exists and not root_exists:
        _validate_recovery_backup(backup, document)
        _durable_replace(backup, catalog_root)
        _remove_path(staging)
        _remove_path(transaction)
        return "restored_prior"
    if backup_exists and root_exists and not staging_exists:
        try:
            _verify_compiled_catalog(
                catalog_root / "generated",
                catalog_root / "catalog-lock.json",
                catalog_path=catalog_root / "catalog.yaml",
                expected_runtime_lock=runtime_lock,
            )
        except (CatalogCompileError, CatalogValidationError):
            _validate_recovery_backup(backup, document)
            _restore_catalog_root(catalog_root, backup)
            recovery_status = "restored_prior"
        else:
            _remove_path(backup)
            recovery_status = "completed_live"
        _remove_path(transaction)
        return recovery_status
    if root_exists and staging_exists and not backup_exists:
        _remove_path(staging)
        _remove_path(transaction)
        return "discarded_unpublished"
    raise CatalogRecoveryError(
        "catalog recovery failed: publication journal describes an impossible or ambiguous state"
    )


def _read_transaction(path: Path, catalog_root: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogRecoveryError(
            "catalog recovery failed: transaction journal is unreadable"
        ) from error
    if not isinstance(value, Mapping) or not verify_digest(value):
        raise CatalogRecoveryError("catalog recovery failed: transaction journal digest is invalid")
    document = dict(value)
    transaction_id = _required_string(document, "transaction_id")
    if (
        document.get("schema_version") != _TRANSACTION_SCHEMA_VERSION
        or document.get("catalog_root") != catalog_root.name
        or path != _transaction_path(catalog_root, transaction_id)
        or document.get("staging")
        != _new_sibling_staging_directory_name(catalog_root, transaction_id)
        or document.get("backup") != _backup_path(catalog_root, transaction_id).name
    ):
        raise CatalogRecoveryError(
            "catalog recovery failed: transaction journal ownership is invalid"
        )
    return document


def _new_sibling_staging_directory_name(catalog_root: Path, transaction_id: str) -> str:
    return f".{catalog_root.name}-compile-stage-{transaction_id}"


def _transaction_runtime_lock(document: Mapping[str, object]) -> dict[str, str | None]:
    runtime_lock = _required_mapping(document.get("runtime_lock"), "runtime_lock")
    path = runtime_lock.get("path")
    digest = runtime_lock.get("sha256")
    if path is not None and not isinstance(path, str):
        raise CatalogRecoveryError(
            "catalog recovery failed: transaction runtime lock path is invalid"
        )
    if digest is not None and not isinstance(digest, str):
        raise CatalogRecoveryError(
            "catalog recovery failed: transaction runtime lock hash is invalid"
        )
    return {"path": path, "sha256": digest}


def _validate_recovery_backup(
    backup: Path,
    transaction: Mapping[str, object],
) -> None:
    prior_lock_sha256 = transaction.get("prior_lock_sha256")
    if prior_lock_sha256 is None:
        source = backup / "catalog.yaml"
        if _optional_catalog_source_sha256(source) != _required_string(
            transaction, "source_sha256"
        ):
            raise CatalogRecoveryError(
                "catalog recovery failed: uncompiled prior source does not match the journal"
            )
        try:
            validate_catalog(source, repository_root=backup)
        except CatalogValidationError as error:
            raise CatalogRecoveryError(
                "catalog recovery failed: prior source cannot be validated"
            ) from error
        return
    if not isinstance(prior_lock_sha256, str):
        raise CatalogRecoveryError("catalog recovery failed: prior lock hash is invalid")
    lock_path = backup / "catalog-lock.json"
    if _optional_file_sha256(lock_path) != prior_lock_sha256:
        raise CatalogRecoveryError(
            "catalog recovery failed: prior lock bytes do not match the journal"
        )
    lock_document, _ = _read_canonical_document(lock_path)
    _verify_compiled_catalog(
        backup / "generated",
        lock_path,
        catalog_path=backup / "catalog.yaml",
        expected_runtime_lock=_runtime_lock_from_catalog_lock(lock_document),
    )


def _runtime_lock_from_catalog_lock(lock: Mapping[str, object]) -> dict[str, str | None]:
    runtime_lock = _required_mapping(lock.get("runtime_lock"), "runtime_lock")
    path = runtime_lock.get("path")
    digest = runtime_lock.get("sha256")
    if path is not None and not isinstance(path, str):
        raise CatalogRecoveryError("catalog recovery failed: sealed runtime lock path is invalid")
    if digest is not None and not isinstance(digest, str):
        raise CatalogRecoveryError("catalog recovery failed: sealed runtime lock hash is invalid")
    return {"path": path, "sha256": digest}


def _publish_catalog_root(
    catalog_root: Path,
    staging: Path,
    *,
    backup: Path,
    after_backup: Callable[[Path], None] | None,
    after_live: Callable[[Path], None] | None,
) -> None:
    try:
        _durable_replace(catalog_root, backup)
        if after_backup is not None:
            after_backup(backup)
        _durable_replace(staging, catalog_root)
        if after_live is not None:
            after_live(catalog_root)
    except BaseException as error:
        if backup.exists():
            _restore_catalog_root(catalog_root, backup)
        if isinstance(error, Exception):
            raise CatalogCompileError("atomic catalog publication failed") from error
        raise


def _restore_catalog_root(catalog_root: Path, backup: Path) -> None:
    failed = catalog_root.parent / f".{catalog_root.name}-compile-failed-{uuid4().hex}"
    try:
        if catalog_root.exists():
            _durable_replace(catalog_root, failed)
        _durable_replace(backup, catalog_root)
    except BaseException as error:
        if isinstance(error, Exception):
            raise CatalogRecoveryError("catalog publication rollback failed") from error
        raise
    _remove_path(failed)


def _durable_replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)
    _fsync_directory(destination.parent)


def _write_bytes_durable(path: Path, data: bytes) -> None:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _durable_replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        if os.name != "nt":
            raise
        return
    try:
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise
    finally:
        os.close(descriptor)


def _catalog_root(catalog_path: Path, output_dir: Path, lock_path: Path) -> Path:
    catalog_root = catalog_path.resolve().parent
    if output_dir.resolve().parent != catalog_root or lock_path.resolve().parent != catalog_root:
        raise CatalogCompileError(
            "catalog, generated output, and lock must be direct children of one catalog root"
        )
    return catalog_root


def _require_same_volume(first: Path, second: Path) -> None:
    first_drive = os.path.splitdrive(str(first.resolve()))[0].casefold()
    second_drive = os.path.splitdrive(str(second.resolve()))[0].casefold()
    if first_drive != second_drive or first.stat().st_dev != second.stat().st_dev:
        raise CatalogCompileError("staging and publication roots must be on the same volume")


def _runtime_lock_reference(path: Path | None) -> dict[str, str | None]:
    if path is None:
        return {"path": None, "sha256": None}
    try:
        return {"path": path.name, "sha256": _sha256(path.read_bytes())}
    except OSError as error:
        raise CatalogCompileError("cannot read runtime lock") from error


def _runtime_lock_reference_or_missing(path: Path | None) -> dict[str, str | None]:
    if path is None:
        return {"path": None, "sha256": None}
    return {"path": path.name, "sha256": _optional_file_sha256(path)}


def _validate_manifest_destination(
    manifest_path: Path,
    catalog_path: Path,
    output_dir: Path,
    lock_path: Path,
    *,
    prior_lock: Path | None,
    runtime_lock: Path | None,
) -> None:
    manifest = manifest_path.resolve()
    protected_paths = {catalog_path.resolve(), lock_path.resolve()}
    if prior_lock is not None:
        protected_paths.add(prior_lock.resolve())
    if runtime_lock is not None:
        protected_paths.add(runtime_lock.resolve())
    if manifest in protected_paths or manifest.is_relative_to(output_dir.resolve()):
        raise CatalogCompileError(
            "command manifest must not replace a catalog input, lock, or generated artifact set"
        )


def _write_command_manifest(
    path: Path,
    *,
    terminal_status: TerminalStatus,
    catalog_source_sha256: str | None,
    catalog_input_sha256: str | None,
    output_sha256: Mapping[str, str],
    protocol_ids: tuple[str, ...],
    runtime_lock: Mapping[str, str | None],
    error_code: str | None,
    recovery_status: str = "not_needed",
) -> None:
    document = attach_digest(
        {
            "schema_version": COMMAND_MANIFEST_SCHEMA_VERSION,
            "manifest_type": "catalog_compile_command",
            "generator_version": GENERATOR_VERSION,
            "terminal_status": terminal_status,
            "catalog_source_sha256": catalog_source_sha256,
            "catalog_input_sha256": catalog_input_sha256,
            "canonical_output_sha256": dict(output_sha256),
            "runtime_lock": dict(runtime_lock),
            "protocol_id": protocol_ids[0] if len(protocol_ids) == 1 else None,
            "protocol_ids": list(protocol_ids),
            "schema_versions": {
                "catalog": COMPILED_SCHEMA_VERSION,
                "task_input": COMPILED_SCHEMA_VERSION,
                "assignment_input": COMPILED_SCHEMA_VERSION,
                "fixture_manifest": COMPILED_SCHEMA_VERSION,
                "traceability": COMPILED_SCHEMA_VERSION,
                "catalog_lock": LOCK_SCHEMA_VERSION,
            },
            "error_code": error_code,
            "recovery_status": recovery_status,
            "unpaid_conformance": UNPAID_CONFORMANCE,
        }
    )
    data = canonical_bytes(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes_durable(path, data)


def _location_document(locations: Mapping[str, SourceLocation], pointer: str) -> dict[str, object]:
    location = locations.get(pointer)
    if location is None:
        raise CatalogCompileError(f"missing stable source location for {pointer}")
    return {
        "path": location.path.replace("\\", "/"),
        "line": location.line,
        "column": location.column,
    }


def _protocol_ids(catalog: Mapping[str, Any]) -> list[str]:
    references = _required_mapping(catalog.get("references"), "references")
    return [_required_string_value(value) for value in _required_list(references, "protocols")]


def _optional_file_sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return _sha256(path.read_bytes())
    except OSError:
        return None


def _catalog_source_sha256(path: Path) -> str:
    return _sha256(_normalized_catalog_source_bytes(path.read_bytes()))


def _optional_catalog_source_sha256(path: Path) -> str | None:
    try:
        return _catalog_source_sha256(path)
    except OSError:
        return None


def _normalized_catalog_source_bytes(data: bytes) -> bytes:
    """Make Git's CRLF checkout conversion invisible to catalog source identity."""

    return data.replace(b"\r\n", b"\n")


def _sha256(data: bytes) -> str:
    return sha256(data).hexdigest()


def _required_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CatalogCompileError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise CatalogCompileError(f"{name} must have string keys")
    return value


def _required_list(mapping: Mapping[str, object], name: str) -> list[object]:
    value = mapping.get(name)
    if not isinstance(value, list):
        raise CatalogCompileError(f"{name} must be a list")
    return value


def _required_string(mapping: Mapping[str, object], name: str) -> str:
    return _required_string_value(mapping.get(name), name)


def _required_string_value(value: object, name: str = "value") -> str:
    if not isinstance(value, str):
        raise CatalogCompileError(f"{name} must be a string")
    return value
