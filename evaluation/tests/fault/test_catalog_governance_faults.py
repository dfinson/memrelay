"""Fault-injection tests for Story 1.4's fixture/eligibility gate split.

AC1 fixture verification is a hard, compile-blocking gate: a missing,
byte-changed, or path-escaping fixture must fail the whole catalog compile
closed, publishing nothing. AC3/AC4 eligibility is always a soft, recorded
disposition: even a scientifically ineligible scenario compiles successfully,
carrying an immutable `rejected` disposition instead of blocking the catalog.
"""

from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path

from memrelay_eval.catalog.compiler import compile_catalog_command

EVALUATION_ROOT = Path(__file__).parents[2]
SOURCE_CATALOG = EVALUATION_ROOT / "catalog" / "catalog.yaml"


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


def run_compile(root: Path):  # noqa: ANN201 - result type is compiler-internal
    paths = compile_paths(root)
    return compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )


def test_byte_changed_fixture_fails_the_whole_compile_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    root = catalog_root(tmp_path)
    fixture_path = root / "fixtures" / "catalog-validation.txt"
    fixture_path.write_bytes(fixture_path.read_bytes() + b"tampered")

    result = run_compile(root)

    assert result.terminal_status == "failed"
    assert result.exit_code == 1
    assert not (compile_paths(root)["output"]).exists()
    assert "FIXTURE_HASH_MISMATCH" in str(result.error)


def test_missing_fixture_file_fails_the_whole_compile(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    (root / "fixtures" / "catalog-validation.txt").unlink()

    result = run_compile(root)

    assert result.terminal_status == "failed"
    assert "FIXTURE_MISSING" in str(result.error)
    assert not (compile_paths(root)["output"]).exists()


def test_path_escaping_fixture_fails_the_whole_compile(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    catalog_path = root / "catalog.yaml"
    content = catalog_path.read_text(encoding="utf-8")
    content = content.replace(
        'source_path: "fixtures/catalog-validation.txt"',
        'source_path: "../outside.txt"',
    )
    catalog_path.write_text(content, encoding="utf-8")

    result = run_compile(root)

    assert result.terminal_status == "failed"
    assert not (compile_paths(root)["output"]).exists()


def test_scientifically_ineligible_scenario_still_compiles_with_a_rejected_disposition(
    tmp_path: Path,
) -> None:
    root = catalog_root(tmp_path)
    catalog_path = root / "catalog.yaml"
    content = catalog_path.read_text(encoding="utf-8")
    content = content.replace("canary_hits: 0", "canary_hits: 5")
    catalog_path.write_text(content, encoding="utf-8")

    result = run_compile(root)

    assert result.terminal_status == "succeeded"
    tasks = json.loads((compile_paths(root)["output"] / "tasks.json").read_text(encoding="utf-8"))
    disposition = tasks["tasks"][0]["eligibility_evaluation"]
    assert disposition["disposition"] == "rejected"
    assert "CANARY_CONTAMINATION" in disposition["codes"]


def test_changed_fixture_bytes_cannot_reuse_a_prior_eligible_disposition(tmp_path: Path) -> None:
    first_root = catalog_root(tmp_path / "first")
    assert run_compile(first_root).terminal_status == "succeeded"
    first_tasks = json.loads(
        (compile_paths(first_root)["output"] / "tasks.json").read_text(encoding="utf-8")
    )
    first_disposition = first_tasks["tasks"][0]["eligibility_evaluation"]

    second_root = catalog_root(tmp_path / "second")
    fixture_path = second_root / "fixtures" / "catalog-validation.txt"
    new_bytes = fixture_path.read_bytes() + b"\nrevised synthetic content\n"
    fixture_path.write_bytes(new_bytes)
    catalog_path = second_root / "catalog.yaml"
    content = catalog_path.read_text(encoding="utf-8")
    content = content.replace(
        "5277786376473628800fc7df15b50a2d596714f457e5901e588174d63eed73f8",
        sha256(new_bytes).hexdigest(),
    )
    catalog_path.write_text(content, encoding="utf-8")

    assert run_compile(second_root).terminal_status == "succeeded"
    second_tasks = json.loads(
        (compile_paths(second_root)["output"] / "tasks.json").read_text(encoding="utf-8")
    )
    second_disposition = second_tasks["tasks"][0]["eligibility_evaluation"]

    assert first_disposition["disposition"] == "eligible"
    assert second_disposition["disposition"] == "eligible"
    assert first_disposition["digest"] != second_disposition["digest"]
    assert first_disposition["fixture_sha256"] != second_disposition["fixture_sha256"]
