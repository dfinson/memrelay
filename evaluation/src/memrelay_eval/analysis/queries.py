"""Verified immutable-Parquet analysis with an ephemeral, closed DuckDB catalog."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Final

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from memrelay_eval.analysis.schemas import (
    ASSIGNED_UNITS_TABLE,
    ELIGIBLE_OUTCOMES_TABLE,
    PARQUET_SCHEMA_VERSION,
    assigned_units_schema,
    eligible_outcomes_schema,
    schema_sha256,
)
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.errors import AnalysisError

DUCKDB_VERSION: Final = "1.5.5"
ANALYSIS_SCHEMA_VERSION: Final = "1.0.0"
_TABLES: Final = frozenset({ASSIGNED_UNITS_TABLE, ELIGIBLE_OUTCOMES_TABLE})
_VERSION_PATTERN: Final = re.compile(
    rf"^parquet-v{re.escape(PARQUET_SCHEMA_VERSION)}-[a-f0-9]{{16}}$"
)
_SHA256_PATTERN: Final = re.compile(r"^[a-f0-9]{64}$")
_COMMON_STRATUM_DIMENSIONS: Final = (
    "stratum",
    "history_mode",
    "model_id",
    "environment_fingerprint_sha256",
    "protocol_sha256",
    "population_id",
)


@dataclass(frozen=True, slots=True)
class AnalysisQuery:
    """A closed projection/filter request; this type intentionally accepts no SQL."""

    table: str
    columns: tuple[str, ...] = ()
    equals: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.table, str) or self.table not in _TABLES:
            raise AnalysisError("analysis_table_not_registered")
        if any(not isinstance(column, str) or not column for column in self.columns):
            raise AnalysisError("analysis_query_columns_invalid")
        if len(set(self.columns)) != len(self.columns):
            raise AnalysisError("analysis_query_columns_duplicate")
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0]
            or not isinstance(item[1], str)
            for item in self.equals
        ):
            raise AnalysisError("analysis_query_filter_invalid")
        keys = tuple(key for key, _ in self.equals)
        if len(set(keys)) != len(keys):
            raise AnalysisError("analysis_query_filters_duplicate")


@dataclass(frozen=True, slots=True)
class DerivationSpec:
    """The non-secret, canonical identity of one derived table or figure."""

    name: str
    derivation_kind: str
    gate_ids: tuple[str, ...] = ()
    parent_derivations: tuple[str, ...] = ()
    query_sha256: str | None = None
    implementation_version: str = ANALYSIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.name or self.derivation_kind not in {"table", "figure", "diagnostic"}:
            raise AnalysisError("derivation_spec_invalid")
        if any(not isinstance(item, str) or not item for item in self.gate_ids) or any(
            not _is_sha256(item) for item in self.parent_derivations
        ):
            raise AnalysisError("derivation_lineage_invalid")
        if self.query_sha256 is not None and not _is_sha256(self.query_sha256):
            raise AnalysisError("derivation_query_identity_invalid")
        object.__setattr__(self, "gate_ids", tuple(sorted(set(self.gate_ids))))
        object.__setattr__(self, "parent_derivations", tuple(sorted(set(self.parent_derivations))))

    def document(self) -> dict[str, object]:
        return {
            "derivation_kind": self.derivation_kind,
            "gate_ids": list(self.gate_ids),
            "implementation_version": self.implementation_version,
            "name": self.name,
            "parent_derivations": list(self.parent_derivations),
            "query_sha256": self.query_sha256,
        }


@dataclass(frozen=True, slots=True)
class FrozenDataset:
    """A verified Story 5.1 dataset loaded wholly before DuckDB registration."""

    root: Path
    directory: Path
    dataset_version: str
    manifest: Mapping[str, object]
    manifest_sha256: str
    tables: Mapping[str, pa.Table]
    table_sha256: Mapping[str, str]

    @classmethod
    def open(cls, allowlisted_root: Path | str, dataset_version: str) -> FrozenDataset:
        root = Path(allowlisted_root).expanduser().resolve()
        if not root.is_dir():
            raise AnalysisError("parquet_allowlisted_root_missing")
        if not _VERSION_PATTERN.fullmatch(dataset_version):
            raise AnalysisError("mutable_or_invalid_dataset_version")
        directory = (root / dataset_version).resolve()
        if directory.parent != root or not directory.is_dir():
            raise AnalysisError("dataset_version_not_published")

        manifest_path = directory / "dataset-manifest.json"
        manifest_bytes = _read_regular_file(manifest_path, "dataset_manifest_missing")
        manifest = _canonical_document(manifest_bytes, "dataset_manifest_not_canonical")
        _verify_dataset_manifest(manifest, dataset_version)
        derivation = _canonical_document(
            _read_regular_file(
                directory / "derivation-manifest.json", "dataset_derivation_lineage_missing"
            ),
            "dataset_derivation_lineage_not_canonical",
        )
        _verify_source_derivation(derivation, manifest, manifest_bytes)
        tables: dict[str, pa.Table] = {}
        hashes: dict[str, str] = {}
        expected_schemas = {
            ASSIGNED_UNITS_TABLE: assigned_units_schema(),
            ELIGIBLE_OUTCOMES_TABLE: eligible_outcomes_schema(),
        }
        files = _mapping(manifest["files"], "dataset_files_invalid")
        schema_hashes = _mapping(manifest["schema_sha256"], "dataset_schema_hashes_invalid")
        for table_name in sorted(_TABLES):
            file_reference = _mapping(files.get(table_name), "dataset_file_reference_invalid")
            if (
                not isinstance(file_reference.get("artifact_id"), str)
                or not file_reference["artifact_id"]
                or not _is_sha256(file_reference.get("sha256"))
                or not isinstance(file_reference.get("size_bytes"), int)
                or file_reference["size_bytes"] < 0
            ):
                raise AnalysisError("dataset_file_reference_invalid", (table_name,))
            file_name = f"{table_name}.parquet"
            data = _read_regular_file(directory / file_name, "dataset_table_missing")
            actual_sha = sha256(data).hexdigest()
            if actual_sha != file_reference.get("sha256") or len(data) != file_reference.get(
                "size_bytes"
            ):
                raise AnalysisError("dataset_table_hash_conflict", (table_name,))
            try:
                table = pq.read_table(pa.BufferReader(data))
            except (pa.ArrowException, OSError, ValueError) as error:
                raise AnalysisError("dataset_table_unreadable", (table_name,)) from error
            expected_schema = expected_schemas[table_name]
            expected_hash = schema_sha256(expected_schema)
            if schema_hashes.get(table_name) != expected_hash or not _parquet_schema_matches(
                table.schema, expected_schema
            ):
                raise AnalysisError("dataset_schema_drift", (table_name,))
            tables[table_name] = table
            hashes[table_name] = actual_sha
        return cls(
            root=root,
            directory=directory,
            dataset_version=dataset_version,
            manifest=manifest,
            manifest_sha256=sha256(manifest_bytes).hexdigest(),
            tables=tables,
            table_sha256=hashes,
        )


class ReadOnlyDuckDbAnalysis:
    """Run only internally generated queries over verified in-memory Arrow registrations."""

    def __init__(self, dataset: FrozenDataset) -> None:
        if duckdb.__version__ != DUCKDB_VERSION:
            raise AnalysisError("duckdb_version_mismatch")
        self.dataset = dataset
        try:
            self._connection = duckdb.connect(
                database=":memory:", config={"enable_external_access": "false"}
            )
        except duckdb.Error as error:
            raise AnalysisError("duckdb_read_only_catalog_unavailable") from error
        try:
            for name in sorted(_TABLES):
                self._connection.register(name, dataset.tables[name])
        except duckdb.Error as error:
            self._connection.close()
            raise AnalysisError("duckdb_table_registration_failed") from error
        self._varying_dimensions = {
            name: _varying_dimensions(table, name) for name, table in dataset.tables.items()
        }

    @classmethod
    def open(cls, allowlisted_root: Path | str, dataset_version: str) -> ReadOnlyDuckDbAnalysis:
        return cls(FrozenDataset.open(allowlisted_root, dataset_version))

    def close(self) -> None:
        """Release the in-memory engine state; no engine state is persisted."""
        self._connection.close()

    def __enter__(self) -> ReadOnlyDuckDbAnalysis:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def read(self, query: AnalysisQuery) -> pa.Table:
        """Return a deterministic projection from one registered logical table."""
        schema = self.dataset.tables[query.table].schema
        selected = query.columns or tuple(field.name for field in schema)
        _require_known_columns(schema, selected)
        filter_columns = tuple(name for name, _ in query.equals)
        _require_known_columns(schema, filter_columns)
        clauses = [f"{_quote_identifier(name)} = ?" for name in filter_columns]
        ordering = _ordering_columns(schema)
        statement = (
            f"SELECT {', '.join(_quote_identifier(name) for name in selected)} "
            f"FROM {_quote_identifier(query.table)}"
        )
        if clauses:
            statement += f" WHERE {' AND '.join(clauses)}"
        statement += f" ORDER BY {', '.join(_quote_identifier(name) for name in ordering)}"
        return self._execute_arrow(statement, [value for _, value in query.equals])

    def count_by(self, table: str, stratify_by: Sequence[str]) -> pa.Table:
        """Count rows only when every varying governed dimension is explicit."""
        if table not in _TABLES:
            raise AnalysisError("analysis_table_not_registered")
        schema = self.dataset.tables[table].schema
        dimensions = tuple(stratify_by)
        if not dimensions or len(set(dimensions)) != len(dimensions):
            raise AnalysisError("stratification_dimensions_invalid")
        _require_known_columns(schema, dimensions)
        required = self._varying_dimensions[table]
        missing = tuple(sorted(required.difference(dimensions)))
        if missing:
            raise AnalysisError("explicit_stratified_operation_required", missing)
        selected = ", ".join(_quote_identifier(name) for name in dimensions)
        statement = (
            f"SELECT {selected}, count(*) AS row_count FROM {_quote_identifier(table)} "
            f"GROUP BY {selected} ORDER BY {selected}"
        )
        return self._execute_arrow(statement, ())

    def _execute_arrow(self, statement: str, parameters: Sequence[str]) -> pa.Table:
        try:
            result = self._connection.execute(statement, list(parameters)).arrow()
            return result.read_all() if isinstance(result, pa.RecordBatchReader) else result
        except duckdb.Error as error:
            raise AnalysisError("closed_analysis_query_failed") from error


@dataclass(frozen=True, slots=True)
class DerivationResult:
    """Content-addressed output and its immutable derivation manifest."""

    derivation_sha256: str
    directory: Path
    output_path: Path
    manifest_path: Path


class DerivationPublisher:
    """Atomically publish reproducible bytes outside immutable Parquet source roots."""

    def __init__(self, output_root: Path | str, dataset: FrozenDataset) -> None:
        self._output_root = Path(output_root).expanduser().resolve()
        if self._output_root.is_relative_to(dataset.root):
            raise AnalysisError("derivation_output_overlaps_immutable_dataset")
        self._dataset = dataset

    def publish_table(self, table: pa.Table, spec: DerivationSpec) -> DerivationResult:
        """Publish canonical table JSON with frozen row order and schema lineage."""
        output = canonical_bytes(
            {
                "columns": table.column_names,
                "rows": [_json_value(row) for row in table.to_pylist()],
            }
        )
        return self._publish(output, "table.json", table.schema, spec)

    def publish_figure(
        self, table: pa.Table, columns: Sequence[str], spec: DerivationSpec
    ) -> DerivationResult:
        """Publish a fixed SVG primitive with deterministic formatting and metadata."""
        _require_known_columns(table.schema, tuple(columns))
        output = deterministic_figure_svg(table, columns)
        return self._publish(output, "figure.svg", table.schema, spec)

    def record_rejection(self, spec: DerivationSpec, reason: AnalysisError) -> Path:
        """Record a typed rejected derivation without fabricating a successful output."""
        basis = {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "artifact_type": "analysis_derivation_rejection",
            "spec": spec.document(),
            "reason_code": reason.code,
            "conflicting_dimensions": list(reason.fields),
            "dataset_version": self._dataset.dataset_version,
            "dataset_manifest_sha256": self._dataset.manifest_sha256,
            "source_table_sha256": dict(sorted(self._dataset.table_sha256.items())),
        }
        document = dict(basis)
        document["rejection_sha256"] = canonical_digest(document)
        target = self._output_root / "rejections" / document["rejection_sha256"]
        _publish_directory(target, {"rejection.json": canonical_bytes(document)})
        return target / "rejection.json"

    def _publish(
        self, output: bytes, filename: str, output_schema: pa.Schema, spec: DerivationSpec
    ) -> DerivationResult:
        context = _derivation_context(self._dataset)
        basis = {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "artifact_type": "analysis_derivation_manifest",
            "spec": spec.document(),
            "dataset_version": self._dataset.dataset_version,
            "dataset_manifest_sha256": self._dataset.manifest_sha256,
            "source_table_sha256": dict(sorted(self._dataset.table_sha256.items())),
            "output_schema_sha256": schema_sha256(output_schema),
            "output_sha256": sha256(output).hexdigest(),
            **context,
        }
        document = dict(basis)
        document["derivation_sha256"] = canonical_digest(document)
        derivation_sha256 = document["derivation_sha256"]
        target = self._output_root / "derivations" / derivation_sha256
        _publish_directory(
            target,
            {filename: output, "derivation-manifest.json": canonical_bytes(document)},
        )
        return DerivationResult(
            derivation_sha256=derivation_sha256,
            directory=target,
            output_path=target / filename,
            manifest_path=target / "derivation-manifest.json",
        )


def deterministic_figure_svg(table: pa.Table, columns: Sequence[str]) -> bytes:
    """Render a small fixed SVG table with stable font, sorting, and numeric representation."""
    _require_known_columns(table.schema, tuple(columns))
    header = " | ".join(columns)
    rows = [
        " | ".join(_display_value(row[column]) for column in columns) for row in table.to_pylist()
    ]
    lines = (header, *rows)
    height = 24 + 18 * len(lines)
    text = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" '
        'viewBox="0 0 1200 ' + str(height) + '">'
    ]
    for index, line in enumerate(lines):
        weight = "700" if index == 0 else "400"
        text.append(
            f'<text x="12" y="{18 + index * 18}" font-family="DejaVu Sans" '
            f'font-size="12" font-weight="{weight}">{html.escape(line)}</text>'
        )
    text.append("</svg>")
    return "".join(text).encode("utf-8")


def _verify_dataset_manifest(manifest: Mapping[str, object], dataset_version: str) -> None:
    if (
        manifest.get("schema_version") != PARQUET_SCHEMA_VERSION
        or manifest.get("artifact_type") != "parquet_dataset_manifest"
        or manifest.get("dataset_version") != dataset_version
    ):
        raise AnalysisError("dataset_manifest_authority_conflict")
    for field in ("materialization_sha256",):
        if not _is_sha256(manifest.get(field)):
            raise AnalysisError("dataset_manifest_hash_invalid", (field,))
    for field in (
        "protocol_sha256",
        "environment_fingerprint_sha256",
        "model_sha256",
        "runtime_lock_sha256",
    ):
        if not _hash_list(manifest.get(field), nonempty=True):
            raise AnalysisError("dataset_manifest_field_invalid", (field,))
    if not _hash_list(manifest.get("source_manifest_sha256"), nonempty=True):
        raise AnalysisError("dataset_source_lineage_missing")
    for field in ("population_id", "stratum", "history_mode"):
        value = manifest.get(field)
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or not item for item in value)
        ):
            raise AnalysisError("dataset_manifest_field_invalid", (field,))
    endpoint_values = manifest.get("endpoint_id")
    if not isinstance(endpoint_values, list) or any(
        not isinstance(item, str) or not item for item in endpoint_values
    ):
        raise AnalysisError("dataset_manifest_field_invalid", ("endpoint_id",))
    if set(_mapping(manifest.get("files"), "dataset_files_invalid")) != _TABLES:
        raise AnalysisError("dataset_manifest_tables_invalid")
    if set(_mapping(manifest.get("schema_sha256"), "dataset_schema_hashes_invalid")) != _TABLES:
        raise AnalysisError("dataset_manifest_schema_tables_invalid")


def _verify_source_derivation(
    derivation: Mapping[str, object], manifest: Mapping[str, object], manifest_bytes: bytes
) -> None:
    required = {
        "schema_version",
        "artifact_type",
        "dataset_manifest_sha256",
        "dataset_version",
        "materialization_sha256",
        "assigned_units_sha256",
        "eligible_outcomes_sha256",
        "source_manifest_sha256",
        "derivation_sha256",
    }
    if set(derivation) != required or derivation.get("schema_version") != PARQUET_SCHEMA_VERSION:
        raise AnalysisError("dataset_derivation_lineage_invalid")
    if (
        derivation.get("artifact_type") != "parquet_derivation_manifest"
        or derivation.get("dataset_version") != manifest["dataset_version"]
        or derivation.get("materialization_sha256") != manifest["materialization_sha256"]
        or derivation.get("dataset_manifest_sha256") != sha256(manifest_bytes).hexdigest()
        or derivation.get("source_manifest_sha256") != manifest["source_manifest_sha256"]
        or derivation.get("assigned_units_sha256")
        != _mapping(manifest["files"], "dataset_files_invalid")[ASSIGNED_UNITS_TABLE]["sha256"]
        or derivation.get("eligible_outcomes_sha256")
        != _mapping(manifest["files"], "dataset_files_invalid")[ELIGIBLE_OUTCOMES_TABLE]["sha256"]
        or derivation.get("derivation_sha256")
        != canonical_digest(derivation, digest_field="derivation_sha256")
    ):
        raise AnalysisError("dataset_derivation_lineage_invalid")


def _hash_list(value: object, *, nonempty: bool) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and len(set(value)) == len(value)
        and all(_is_sha256(item) for item in value)
    )


def _derivation_context(dataset: FrozenDataset) -> dict[str, object]:
    manifest = dataset.manifest
    units: dict[str, object] = {}
    for name, table in dataset.tables.items():
        metadata = table.schema.metadata or {}
        encoded = metadata.get(b"memrelay.units")
        if encoded is None:
            raise AnalysisError("dataset_units_metadata_missing", (name,))
        try:
            units[name] = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AnalysisError("dataset_units_metadata_invalid", (name,)) from error
    return {
        "protocol_sha256": manifest["protocol_sha256"],
        "population_id": manifest["population_id"],
        "endpoint_id": manifest["endpoint_id"],
        "stratum": manifest["stratum"],
        "history_mode": manifest["history_mode"],
        "runtime_lock_sha256": manifest["runtime_lock_sha256"],
        "units": units,
    }


def _varying_dimensions(table: pa.Table, table_name: str) -> frozenset[str]:
    dimensions = list(_COMMON_STRATUM_DIMENSIONS)
    if table_name == ELIGIBLE_OUTCOMES_TABLE:
        dimensions.append("endpoint_id")
    return frozenset(
        name
        for name in dimensions
        if name in table.column_names and len(set(table[name].to_pylist())) > 1
    )


def _ordering_columns(schema: pa.Schema) -> tuple[str, ...]:
    metadata = schema.metadata or {}
    try:
        values = json.loads(metadata[b"memrelay.ordering_keys"].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("dataset_ordering_metadata_invalid") from error
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise AnalysisError("dataset_ordering_metadata_invalid")
    columns = tuple(values)
    _require_known_columns(schema, columns)
    return columns


def _require_known_columns(schema: pa.Schema, columns: Sequence[str]) -> None:
    known = set(schema.names)
    invalid = tuple(sorted(name for name in columns if name not in known))
    if invalid:
        raise AnalysisError("analysis_query_column_not_allowed", invalid)


def _parquet_schema_matches(actual: pa.Schema, expected: pa.Schema) -> bool:
    """Validate Parquet's dictionary-decoded reader schema against the frozen Arrow contract."""
    if actual.names != expected.names or actual.metadata != expected.metadata:
        return False
    return all(
        actual_field.nullable == expected_field.nullable
        and _parquet_type_matches(actual_field.type, expected_field.type)
        for actual_field, expected_field in zip(actual, expected, strict=True)
    )


def _parquet_type_matches(actual: pa.DataType, expected: pa.DataType) -> bool:
    if pa.types.is_dictionary(expected):
        return actual == expected or actual == expected.value_type
    if pa.types.is_list(expected):
        return pa.types.is_list(actual) and _parquet_type_matches(
            actual.value_type, expected.value_type
        )
    if pa.types.is_struct(expected):
        return (
            pa.types.is_struct(actual)
            and actual.num_fields == expected.num_fields
            and all(
                actual[index].name == expected[index].name
                and actual[index].nullable == expected[index].nullable
                and _parquet_type_matches(actual[index].type, expected[index].type)
                for index in range(expected.num_fields)
            )
        )
    return actual == expected


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _canonical_document(data: bytes, code: str) -> Mapping[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise AnalysisError(code)
            result[key] = value
        return result

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError(code) from error
    if not isinstance(document, dict) or canonical_bytes(document) != data:
        raise AnalysisError(code)
    return document


def _read_regular_file(path: Path, code: str) -> bytes:
    try:
        if not path.is_file() or path.is_symlink():
            raise AnalysisError(code)
        return path.read_bytes()
    except OSError as error:
        raise AnalysisError(code) from error


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AnalysisError(code)
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_PATTERN.fullmatch(value))


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _json_value(nested) for key, nested in value.items()}
    if isinstance(value, Sequence):
        return [_json_value(item) for item in value]
    raise AnalysisError("derivation_output_value_unsupported")


def _display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return format(value, ".12g")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _publish_directory(target: Path, files: Mapping[str, bytes]) -> None:
    if target.exists():
        for name, expected in files.items():
            if _read_regular_file(target / name, "published_derivation_conflict") != expected:
                raise AnalysisError("published_derivation_conflict")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.{uuid.uuid4().hex}.staging"
    try:
        staging.mkdir()
        for name, data in files.items():
            path = staging / name
            path.write_bytes(data)
            with path.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
        try:
            os.replace(staging, target)
        except OSError as error:
            if target.is_dir():
                for name, expected in files.items():
                    if (
                        _read_regular_file(target / name, "published_derivation_conflict")
                        != expected
                    ):
                        raise AnalysisError("published_derivation_conflict") from error
            else:
                raise AnalysisError("derivation_publication_failed") from error
    finally:
        if staging.exists():
            shutil.rmtree(staging)
