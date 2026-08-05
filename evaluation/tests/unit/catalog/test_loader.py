from __future__ import annotations

from pathlib import Path

import pytest
from memrelay_eval.catalog.loader import CatalogLoadError, load_catalog


def write_catalog(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loader_returns_data_and_repository_relative_source_locations(tmp_path: Path) -> None:
    catalog = write_catalog(
        tmp_path,
        """\
catalog:
  scenarios:
    - title: "A source-located scenario"
""",
    )

    loaded = load_catalog(catalog, repository_root=tmp_path)

    assert loaded.data["catalog"]["scenarios"][0]["title"] == "A source-located scenario"
    assert loaded.source_path == "catalog.yaml"
    assert loaded.locations["/catalog/scenarios/0/title"].path == "catalog.yaml"
    assert loaded.locations["/catalog/scenarios/0/title"].line == 3
    assert loaded.locations["/catalog/scenarios/0/title"].column == 14


def test_loader_converts_parse_errors_to_source_located_diagnostics(tmp_path: Path) -> None:
    with pytest.raises(CatalogLoadError) as raised:
        load_catalog(write_catalog(tmp_path, "catalog:\n  scenarios: [\n"))

    diagnostic = raised.value.diagnostics[0]
    assert diagnostic.code == "CATALOG_YAML_SYNTAX"
    assert diagnostic.path == "catalog.yaml"
    assert diagnostic.line is not None
    assert diagnostic.column is not None


@pytest.mark.parametrize(
    ("content", "line", "column"),
    [
        (
            """\
references:
  fixtures:
    - id: fixture_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      id: fixture_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
""",
            4,
            7,
        ),
        (
            """\
scenarios:
  - injected_conditions:
      - id: condition_cccccccccccccccccccccccccccccccc
        description: first
        description: second
""",
            5,
            9,
        ),
        (
            """\
scenarios:
  - resource_limits:
      wall_seconds: 30
      wall_seconds: 60
""",
            4,
            7,
        ),
    ],
)
def test_loader_rejects_nested_duplicate_keys_with_exact_locations(
    tmp_path: Path, content: str, line: int, column: int
) -> None:

    with pytest.raises(CatalogLoadError) as raised:
        load_catalog(write_catalog(tmp_path, content))

    diagnostic = raised.value.diagnostics[0]
    assert diagnostic.code == "CATALOG_DUPLICATE_KEY"
    assert (diagnostic.line, diagnostic.column) == (line, column)


@pytest.mark.parametrize(
    ("content", "line", "column"),
    [
        ("1: value\n", 1, 1),
        ("true: value\n", 1, 1),
    ],
)
def test_loader_rejects_non_string_scalar_mapping_keys(
    tmp_path: Path, content: str, line: int, column: int
) -> None:
    with pytest.raises(CatalogLoadError) as raised:
        load_catalog(write_catalog(tmp_path, content))

    diagnostic = raised.value.diagnostics[0]
    assert diagnostic.code == "CATALOG_YAML_FEATURE"
    assert (diagnostic.line, diagnostic.column) == (line, column)


def test_loader_does_not_modify_a_prior_lock_file(tmp_path: Path) -> None:
    catalog = write_catalog(tmp_path, "catalog: {}\n")
    prior_lock = tmp_path / "catalog-lock.json"
    prior_lock.write_text('{"catalog_version":"1.0.0"}', encoding="utf-8")

    load_catalog(catalog, repository_root=tmp_path)

    assert prior_lock.read_text(encoding="utf-8") == '{"catalog_version":"1.0.0"}'
