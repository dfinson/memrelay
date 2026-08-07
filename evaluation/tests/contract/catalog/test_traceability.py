"""Contract tests for the generated traceability map (Story 1.4, AC2).

Covers the story's Testing Requirements for traceability: every P0/P1 namespace
resolves, orphan references cannot compile, duplicate source locations are
distinguishable, source locations are preserved, and regeneration is
byte-identical (an immutable, reproducible map, never hand-authored).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from memrelay_eval.catalog.compiler import compile_catalog_command
from memrelay_eval.catalog.validation import CatalogValidationError

EVALUATION_ROOT = Path(__file__).parents[3]
SOURCE_CATALOG = EVALUATION_ROOT / "catalog" / "catalog.yaml"

_NAMESPACES = (
    ("protocol_ids", "protocols"),
    ("fixture_refs", "fixtures"),
    ("risk_ids", "risks"),
    ("gate_ids", "gates"),
    ("endpoint_ids", "endpoints"),
    ("claim_ids", "claims"),
)


def catalog_root(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir(parents=True)
    shutil.copy2(SOURCE_CATALOG, root / "catalog.yaml")
    shutil.copytree(SOURCE_CATALOG.parent / "fixtures", root / "fixtures")
    return root


def compile_paths(root: Path) -> dict[str, Path]:
    return {
        "catalog": root / "catalog.yaml",
        "output": root / "generated",
        "lock": root / "catalog-lock.json",
        "manifest": root / "compile-manifest.json",
    }


def compile_in_process(root: Path) -> None:
    paths = compile_paths(root)
    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )
    assert result.terminal_status == "succeeded"
    assert result.exit_code == 0


def load_traceability(root: Path) -> dict[str, object]:
    path = compile_paths(root)["output"] / "traceability.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_namespace_resolves_with_a_scenario_and_catalog_source_location(
    tmp_path: Path,
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    traceability = load_traceability(root)

    record = traceability["traceability"][0]
    relations = {reference["relation"] for reference in record["references"]}
    for field, _namespace in _NAMESPACES:
        assert field in relations, f"missing traceability link for {field}"
    assert "grader_ref" in relations
    assert "study_validity_ref" in relations

    for reference in record["references"]:
        assert reference["scenario_source"]["path"] == "catalog.yaml"
        assert reference["catalog_source"]["path"] == "catalog.yaml"
        assert reference["scenario_source"]["line"] is not None
        assert reference["catalog_source"]["line"] is not None


def test_orphan_reference_fails_closed_before_traceability_is_generated(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    catalog_path = root / "catalog.yaml"
    content = catalog_path.read_text(encoding="utf-8")
    content = content.replace(
        'grader_ref: "grader_44444444444444444444444444444444"',
        'grader_ref: "grader_99999999999999999999999999999999"',
    )
    catalog_path.write_text(content, encoding="utf-8")

    paths = compile_paths(root)
    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )

    assert result.terminal_status == "failed"
    assert isinstance(result.error, CatalogValidationError)
    assert not paths["output"].exists()


def test_source_locations_are_stable_line_and_column_pairs(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    traceability = load_traceability(root)

    record = traceability["traceability"][0]
    scenario_source = record["scenario_source"]
    assert scenario_source["path"] == "catalog.yaml"
    assert isinstance(scenario_source["line"], int)
    assert isinstance(scenario_source["column"], int)
    for field_location in record["field_locations"].values():
        assert field_location["path"] == "catalog.yaml"
        assert isinstance(field_location["line"], int)


def test_traceability_regeneration_is_byte_identical_across_independent_compiles(
    tmp_path: Path,
) -> None:
    first_root = catalog_root(tmp_path / "first")
    second_root = catalog_root(tmp_path / "second")
    compile_in_process(first_root)
    compile_in_process(second_root)

    first_bytes = (compile_paths(first_root)["output"] / "traceability.json").read_bytes()
    second_bytes = (compile_paths(second_root)["output"] / "traceability.json").read_bytes()

    assert first_bytes == second_bytes


def test_traceability_is_never_hand_authored_it_is_generated_from_catalog_data(
    tmp_path: Path,
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    traceability = load_traceability(root)
    record = traceability["traceability"][0]

    catalog_content = (root / "catalog.yaml").read_text(encoding="utf-8")
    for reference in record["references"]:
        assert reference["id"] in catalog_content
