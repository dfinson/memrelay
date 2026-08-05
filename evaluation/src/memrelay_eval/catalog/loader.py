"""Safe YAML loading with source-location preservation for the authored catalog."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.error import MarkedYAMLError
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken

from memrelay_eval.domain.errors import DomainError

_ENV_SUBSTITUTION = re.compile(r"\$\{[^}]+\}")


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """A repository-relative source location suitable for stable diagnostics."""

    path: str
    line: int | None
    column: int | None


@dataclass(frozen=True, slots=True)
class CatalogDiagnostic:
    """A stable, source-located catalog validation diagnostic."""

    code: str
    message: str
    path: str
    line: int | None
    column: int | None

    def sort_key(self) -> tuple[str, int, int, str, str]:
        return (
            self.path,
            self.line if self.line is not None else 0,
            self.column if self.column is not None else 0,
            self.code,
            self.message,
        )

    def __str__(self) -> str:
        location = self.path
        if self.line is not None and self.column is not None:
            location = f"{location}:{self.line}:{self.column}"
        return f"{location}: {self.code}: {self.message}"


class CatalogLoadError(DomainError):
    """The authored YAML cannot safely become a catalog document."""

    def __init__(self, diagnostics: tuple[CatalogDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("\n".join(str(diagnostic) for diagnostic in diagnostics))


@dataclass(frozen=True, slots=True)
class LoadedCatalog:
    """Parsed catalog data and JSON-pointer source locations."""

    data: Mapping[str, Any]
    locations: Mapping[str, SourceLocation]
    source_path: str


class _DuplicateKeyError(Exception):
    def __init__(self, key: str, mark: Any) -> None:
        self.key = key
        self.mark = mark


class _InvalidMappingKeyError(Exception):
    def __init__(self, message: str, mark: Any) -> None:
        self.message = message
        self.mark = mark


class _DuplicateKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _DuplicateKeyLoader, node: MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        if not isinstance(key_node, ScalarNode):
            raise _InvalidMappingKeyError(
                "mapping keys must be scalar strings", key_node.start_mark
            )
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise _InvalidMappingKeyError("mapping keys must be strings", key_node.start_mark)
        if key in mapping:
            raise _DuplicateKeyError(str(key), key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _display_path(path: Path, repository_root: Path | None) -> str:
    resolved = path.resolve()
    if repository_root is not None:
        try:
            return resolved.relative_to(repository_root.resolve()).as_posix()
        except ValueError:
            pass
    return path.name


def _location(path: str, mark: Any | None) -> SourceLocation:
    if mark is None:
        return SourceLocation(path, None, None)
    return SourceLocation(path, mark.line + 1, mark.column + 1)


def _pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _collect_locations(
    node: Node, pointer: str, path: str, locations: dict[str, SourceLocation]
) -> None:
    locations[pointer] = _location(path, node.start_mark)
    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            if isinstance(key_node, ScalarNode):
                _collect_locations(
                    value_node,
                    f"{pointer}/{_pointer_segment(str(key_node.value))}",
                    path,
                    locations,
                )
    elif isinstance(node, SequenceNode):
        for index, value_node in enumerate(node.value):
            _collect_locations(value_node, f"{pointer}/{index}", path, locations)


def _walk_forbidden_nodes(
    node: Node,
    pointer: str,
    path: str,
    diagnostics: list[CatalogDiagnostic],
) -> None:
    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            if isinstance(key_node, ScalarNode) and key_node.value == "<<":
                location = _location(path, key_node.start_mark)
                diagnostics.append(
                    CatalogDiagnostic(
                        "CATALOG_YAML_FEATURE",
                        "YAML merge keys are forbidden",
                        location.path,
                        location.line,
                        location.column,
                    )
                )
            if isinstance(key_node, ScalarNode):
                _walk_forbidden_nodes(
                    value_node,
                    f"{pointer}/{_pointer_segment(str(key_node.value))}",
                    path,
                    diagnostics,
                )
    elif isinstance(node, SequenceNode):
        for index, value_node in enumerate(node.value):
            _walk_forbidden_nodes(value_node, f"{pointer}/{index}", path, diagnostics)
    elif (
        isinstance(node, ScalarNode)
        and node.tag.endswith(":str")
        and _ENV_SUBSTITUTION.search(node.value)
    ):
        location = _location(path, node.start_mark)
        diagnostics.append(
            CatalogDiagnostic(
                "CATALOG_ENV_SUBSTITUTION",
                "environment substitution is forbidden in catalog YAML",
                location.path,
                location.line,
                location.column,
            )
        )


def _walk_non_finite(
    value: Any,
    pointer: str,
    locations: Mapping[str, SourceLocation],
    diagnostics: list[CatalogDiagnostic],
) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        location = locations.get(pointer, locations[""])
        diagnostics.append(
            CatalogDiagnostic(
                "CATALOG_NON_FINITE_NUMBER",
                "NaN and Infinity are forbidden",
                location.path,
                location.line,
                location.column,
            )
        )
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            _walk_non_finite(
                nested,
                f"{pointer}/{_pointer_segment(str(key))}",
                locations,
                diagnostics,
            )
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _walk_non_finite(nested, f"{pointer}/{index}", locations, diagnostics)


def load_catalog(path: Path, *, repository_root: Path | None = None) -> LoadedCatalog:
    """Load a UTF-8 catalog after rejecting YAML features with non-local semantics."""

    source_path = _display_path(path, repository_root)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CatalogLoadError(
            (CatalogDiagnostic("CATALOG_IO", str(error), source_path, None, None),)
        ) from error
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CatalogLoadError(
            (
                CatalogDiagnostic(
                    "CATALOG_ENCODING",
                    "catalog input must be UTF-8",
                    source_path,
                    error.start + 1,
                    None,
                ),
            )
        ) from error

    try:
        tokens = list(yaml.scan(text))
        forbidden_token = next(
            (token for token in tokens if isinstance(token, (AnchorToken, AliasToken))),
            None,
        )
        if forbidden_token is not None:
            location = _location(source_path, forbidden_token.start_mark)
            raise CatalogLoadError(
                (
                    CatalogDiagnostic(
                        "CATALOG_YAML_FEATURE",
                        "YAML anchors and aliases are forbidden",
                        location.path,
                        location.line,
                        location.column,
                    ),
                )
            )
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        if root is None:
            raise CatalogLoadError(
                (
                    CatalogDiagnostic(
                        "CATALOG_SCHEMA", "catalog must not be empty", source_path, 1, 1
                    ),
                )
            )
        locations: dict[str, SourceLocation] = {}
        _collect_locations(root, "", source_path, locations)
        diagnostics: list[CatalogDiagnostic] = []
        _walk_forbidden_nodes(root, "", source_path, diagnostics)
        if diagnostics:
            raise CatalogLoadError(tuple(sorted(diagnostics, key=CatalogDiagnostic.sort_key)))
        data = yaml.load(text, Loader=_DuplicateKeyLoader)
    except CatalogLoadError:
        raise
    except _DuplicateKeyError as error:
        location = _location(source_path, error.mark)
        raise CatalogLoadError(
            (
                CatalogDiagnostic(
                    "CATALOG_DUPLICATE_KEY",
                    f"duplicate key {error.key!r}",
                    location.path,
                    location.line,
                    location.column,
                ),
            )
        ) from error
    except _InvalidMappingKeyError as error:
        location = _location(source_path, error.mark)
        raise CatalogLoadError(
            (
                CatalogDiagnostic(
                    "CATALOG_YAML_FEATURE",
                    error.message,
                    location.path,
                    location.line,
                    location.column,
                ),
            )
        ) from error
    except MarkedYAMLError as error:
        location = _location(source_path, error.problem_mark or error.context_mark)
        raise CatalogLoadError(
            (
                CatalogDiagnostic(
                    "CATALOG_YAML_SYNTAX",
                    error.problem or "invalid YAML",
                    location.path,
                    location.line,
                    location.column,
                ),
            )
        ) from error

    if not isinstance(data, Mapping):
        location = locations[""]
        raise CatalogLoadError(
            (
                CatalogDiagnostic(
                    "CATALOG_SCHEMA",
                    "catalog root must be an object",
                    location.path,
                    location.line,
                    location.column,
                ),
            )
        )
    _walk_non_finite(data, "", locations, diagnostics := [])
    if diagnostics:
        raise CatalogLoadError(tuple(sorted(diagnostics, key=CatalogDiagnostic.sort_key)))
    return LoadedCatalog(data, locations, source_path)
