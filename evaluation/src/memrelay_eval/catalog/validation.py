"""Structural, semantic, and version validation for the authored scenario catalog."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from memrelay_eval.domain.errors import DomainError

from .canonical import canonical_bytes
from .loader import CatalogDiagnostic, CatalogLoadError, LoadedCatalog, SourceLocation, load_catalog

ChangeKind = Literal["none", "content", "additive", "breaking"]
_REFERENCE_FIELDS = {
    "protocol_ids": "protocols",
    "fixture_refs": "fixtures",
    "risk_ids": "risks",
    "gate_ids": "gates",
    "endpoint_ids": "endpoints",
    "expected_evidence": "evidence",
    "claim_ids": "claims",
}
_BREAKING_SCENARIO_FIELDS = frozenset(
    {
        "protocol_ids",
        "priority",
        "fixture_refs",
        "injected_conditions",
        "procedure",
        "expected_evidence",
        "pass_criteria",
        "allowed_retries",
        "risk_ids",
        "gate_ids",
        "endpoint_ids",
        "claim_ids",
        "data_classification",
        "network_policy",
        "resource_limits",
        "grader_ref",
    }
)


class CatalogValidationError(DomainError):
    """A catalog is structurally invalid, semantically open, or version-invalid."""

    def __init__(self, diagnostics: tuple[CatalogDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("\n".join(str(diagnostic) for diagnostic in diagnostics))


@dataclass(frozen=True, slots=True)
class CatalogValidationResult:
    """Validated authored source. Compilation and lock generation remain later-story work."""

    catalog: Mapping[str, Any]
    source_path: str
    semantic_json: str
    semantic_source: Mapping[str, Any]
    locations: Mapping[str, SourceLocation]
    change_kind: ChangeKind = "none"


def _load_schema() -> Mapping[str, Any]:
    resource = files("memrelay_eval").joinpath("schemas", "scenario.schema.json")
    if resource.is_file():
        return json.loads(resource.read_text(encoding="utf-8"))
    source_schema = Path(__file__).parents[3] / "schemas" / "scenario.schema.json"
    return json.loads(source_schema.read_text(encoding="utf-8"))


def _default_repository_root(path: Path) -> Path:
    working_directory = Path.cwd().resolve()
    try:
        path.resolve().relative_to(working_directory)
    except ValueError:
        return path.resolve().parent
    return working_directory


def _pointer(parts: object) -> str:
    return "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in parts)


def _location(loaded: LoadedCatalog, pointer: str) -> SourceLocation:
    current = pointer
    while current not in loaded.locations and current:
        current = current.rsplit("/", 1)[0]
    return loaded.locations.get(current, loaded.locations[""])


def _diagnostic(
    loaded: LoadedCatalog,
    code: str,
    message: str,
    pointer: str = "",
) -> CatalogDiagnostic:
    location = _location(loaded, pointer)
    return CatalogDiagnostic(code, message, location.path, location.line, location.column)


def _schema_diagnostics(
    loaded: LoadedCatalog, schema: Mapping[str, Any]
) -> list[CatalogDiagnostic]:
    validator = Draft202012Validator(schema)
    diagnostics: list[CatalogDiagnostic] = []
    for error in sorted(
        validator.iter_errors(loaded.data), key=lambda item: list(item.absolute_path)
    ):
        pointer = _pointer(error.absolute_path)
        diagnostics.append(_diagnostic(loaded, "CATALOG_SCHEMA", error.message, pointer))
    return diagnostics


def _reference_ids(reference: Any) -> set[str]:
    if not isinstance(reference, list):
        return set()
    return {item["id"] if isinstance(item, Mapping) else item for item in reference}


def _semantic_diagnostics(loaded: LoadedCatalog) -> list[CatalogDiagnostic]:
    catalog = loaded.data
    diagnostics: list[CatalogDiagnostic] = []
    references = catalog.get("references", {})
    reference_sets = {
        namespace: _reference_ids(values)
        for namespace, values in references.items()
        if isinstance(namespace, str)
    }

    declared_ids: dict[str, str] = {}

    def register(identifier: str, pointer: str) -> None:
        previous = declared_ids.get(identifier)
        if previous is not None:
            diagnostics.append(
                _diagnostic(
                    loaded,
                    "CATALOG_DUPLICATE_ID",
                    f"duplicate identifier {identifier!r}; first declared at {previous or '/'}",
                    pointer,
                )
            )
        else:
            declared_ids[identifier] = pointer

    catalog_id = catalog.get("catalog_id")
    if isinstance(catalog_id, str):
        register(catalog_id, "/catalog_id")
    for namespace, values in references.items():
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            identifier = value.get("id") if isinstance(value, Mapping) else value
            if isinstance(identifier, str):
                register(
                    identifier,
                    f"/references/{namespace}/{index}"
                    + ("/id" if isinstance(value, Mapping) else ""),
                )

    scenarios = catalog.get("scenarios", [])
    if not isinstance(scenarios, list):
        return diagnostics
    for scenario_index, scenario in enumerate(scenarios):
        if not isinstance(scenario, Mapping):
            continue
        prefix = f"/scenarios/{scenario_index}"
        scenario_id = scenario.get("id")
        if isinstance(scenario_id, str):
            register(scenario_id, f"{prefix}/id")
        for nested_name in ("injected_conditions",):
            nested_values = scenario.get(nested_name, [])
            if isinstance(nested_values, list):
                for nested_index, nested in enumerate(nested_values):
                    if isinstance(nested, Mapping) and isinstance(nested.get("id"), str):
                        register(nested["id"], f"{prefix}/{nested_name}/{nested_index}/id")
        for nested_name in ("procedure", "pass_criteria"):
            nested = scenario.get(nested_name)
            if isinstance(nested, Mapping) and isinstance(nested.get("id"), str):
                register(nested["id"], f"{prefix}/{nested_name}/id")

        for field, namespace in _REFERENCE_FIELDS.items():
            values = scenario.get(field, [])
            if not isinstance(values, list):
                continue
            for value_index, identifier in enumerate(values):
                if identifier not in reference_sets.get(namespace, set()):
                    diagnostics.append(
                        _diagnostic(
                            loaded,
                            "CATALOG_UNRESOLVED_REFERENCE",
                            f"{field} references undeclared {namespace} identifier {identifier!r}",
                            f"{prefix}/{field}/{value_index}",
                        )
                    )
        grader = scenario.get("grader_ref")
        if grader not in reference_sets.get("graders", set()):
            diagnostics.append(
                _diagnostic(
                    loaded,
                    "CATALOG_UNRESOLVED_REFERENCE",
                    f"grader_ref references undeclared graders identifier {grader!r}",
                    f"{prefix}/grader_ref",
                )
            )
    return diagnostics


def _semantic_projection(catalog: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in catalog.items() if key != "catalog_version"}


def _parse_version(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    pieces = value.split(".")
    if len(pieces) != 3 or any(not piece.isdecimal() for piece in pieces):
        return None
    return tuple(int(piece) for piece in pieces)  # type: ignore[return-value]


def _scenario_map(projection: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    scenarios = projection.get("scenarios", [])
    if not isinstance(scenarios, list):
        return {}
    return {
        scenario["id"]: scenario
        for scenario in scenarios
        if isinstance(scenario, Mapping) and isinstance(scenario.get("id"), str)
    }


def _reference_projection(projection: Mapping[str, Any]) -> Mapping[str, Any]:
    value = projection.get("references", {})
    return value if isinstance(value, Mapping) else {}


def _classify_change(previous: Mapping[str, Any], current: Mapping[str, Any]) -> ChangeKind:
    if previous == current:
        return "none"
    previous_scenarios = _scenario_map(previous)
    current_scenarios = _scenario_map(current)
    if set(previous_scenarios) - set(current_scenarios):
        return "breaking"
    for identifier in set(previous_scenarios) & set(current_scenarios):
        before = previous_scenarios[identifier]
        after = current_scenarios[identifier]
        if any(before.get(field) != after.get(field) for field in _BREAKING_SCENARIO_FIELDS):
            return "breaking"

    previous_references = _reference_projection(previous)
    current_references = _reference_projection(current)
    for namespace, values in previous_references.items():
        if namespace not in current_references:
            return "breaking"
        if not isinstance(values, list) or not isinstance(current_references[namespace], list):
            return "breaking"
        previous_values = {
            value.get("id") if isinstance(value, Mapping) else value: value for value in values
        }
        current_values = {
            value.get("id") if isinstance(value, Mapping) else value: value
            for value in current_references[namespace]
        }
        if set(previous_values) - set(current_values):
            return "breaking"
        if any(current_values[key] != value for key, value in previous_values.items()):
            return "breaking"
    if set(current_scenarios) - set(previous_scenarios):
        return "additive"
    if any(namespace not in previous_references for namespace in current_references):
        return "additive"
    for namespace, values in current_references.items():
        previous_values = previous_references.get(namespace, [])
        if (
            isinstance(values, list)
            and isinstance(previous_values, list)
            and len(values) > len(previous_values)
        ):
            return "additive"
    return "content"


def _version_diagnostic(
    loaded: LoadedCatalog,
    prior_lock: Path,
    semantic_projection: Mapping[str, Any],
) -> ChangeKind | CatalogDiagnostic:
    try:
        lock = json.loads(prior_lock.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return _diagnostic(loaded, "CATALOG_LOCK", f"cannot read prior catalog lock: {error}")
    if not isinstance(lock, Mapping):
        return _diagnostic(loaded, "CATALOG_LOCK", "prior catalog lock must be an object")
    previous_projection = lock.get("semantic_source")
    previous_version = _parse_version(lock.get("catalog_version"))
    current_version = _parse_version(loaded.data.get("catalog_version"))
    if (
        not isinstance(previous_projection, Mapping)
        or previous_version is None
        or current_version is None
    ):
        return _diagnostic(
            loaded,
            "CATALOG_LOCK",
            "prior catalog lock requires semantic_source and catalog_version X.Y.Z",
        )
    kind = _classify_change(previous_projection, semantic_projection)
    expected = {
        "none": previous_version,
        "content": (previous_version[0], previous_version[1], previous_version[2] + 1),
        "additive": (previous_version[0], previous_version[1] + 1, 0),
        "breaking": (previous_version[0] + 1, 0, 0),
    }[kind]
    if current_version != expected:
        return _diagnostic(
            loaded,
            "CATALOG_VERSION_POLICY",
            f"{kind} catalog changes require version {'.'.join(map(str, expected))}, "
            f"not {loaded.data.get('catalog_version')!r}",
            "/catalog_version",
        )
    return kind


def validate_catalog(
    path: Path,
    *,
    prior_lock: Path | None = None,
    repository_root: Path | None = None,
) -> CatalogValidationResult:
    """Validate one authored YAML catalog without writing artifacts or locks."""

    try:
        loaded = load_catalog(
            path,
            repository_root=repository_root or _default_repository_root(path),
        )
    except CatalogLoadError as error:
        raise CatalogValidationError(error.diagnostics) from error
    schema = _load_schema()
    diagnostics = _schema_diagnostics(loaded, schema)
    if not diagnostics:
        diagnostics.extend(_semantic_diagnostics(loaded))
    if diagnostics:
        raise CatalogValidationError(tuple(sorted(diagnostics, key=CatalogDiagnostic.sort_key)))

    projection = _semantic_projection(loaded.data)
    semantic_json = canonical_bytes(projection).decode("utf-8")
    change_kind: ChangeKind = "none"
    if prior_lock is not None:
        policy_result = _version_diagnostic(loaded, prior_lock, projection)
        if isinstance(policy_result, CatalogDiagnostic):
            raise CatalogValidationError((policy_result,))
        change_kind = policy_result
    return CatalogValidationResult(
        loaded.data,
        loaded.source_path,
        semantic_json,
        projection,
        loaded.locations,
        change_kind,
    )
